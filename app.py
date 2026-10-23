"""FastAPI service for the NestBasket customer-support RAG assistant.

Endpoints
  GET  /health  - is the API process running and is the index loaded?
  POST /ask     - answer a customer question from the FAQ

Run locally:   uvicorn app:app --host 127.0.0.1 --port 8000
Interactive documentation: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

load_dotenv()  # reads a local .env file if present; variables already set in the environment take priority

from support_rag import BackendError, Settings, SupportRAG  # noqa: E402

MAX_QUESTION_CHARS = 1000
LOG_FILE = os.getenv("LOG_FILE", "logs/app.log")


# --------------------------------------------------------------------------- logging
class RedactSecretsFilter(logging.Filter):
    """Defence in depth: replace any configured secret value that reaches a log line."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 8]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            message = record.getMessage()
            for secret in self.secrets:
                message = message.replace(secret, "[REDACTED]")
            record.msg, record.args = message, None
        return True


def setup_logging() -> None:
    logger = logging.getLogger("support_api")
    if logger.handlers:          # already configured (e.g. module re-imported)
        return
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    redact = RedactSecretsFilter([os.getenv("LLM_API_KEY", "")])
    os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
    for handler in (logging.StreamHandler(), RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)):
        handler.setFormatter(fmt)
        handler.addFilter(redact)
        logger.addHandler(handler)
    logger.propagate = False


setup_logging()
log = logging.getLogger("support_api.app")


# --------------------------------------------------------------------------- schemas
class AskRequest(BaseModel):
    question: str = Field(
        ...,
        description=f"Customer question (1 to {MAX_QUESTION_CHARS} characters after trimming spaces).",
        examples=["How many days do I have to return an unused item?"],
    )

    @field_validator("question")
    @classmethod
    def check_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question must not be empty.")
        if len(value) > MAX_QUESTION_CHARS:
            raise ValueError(f"Question must be at most {MAX_QUESTION_CHARS} characters (received {len(value)}).")
        return value


class Source(BaseModel):
    section: str
    distance: float = Field(description="Squared L2 distance between question and passage (lower = closer).")


class Latency(BaseModel):
    retrieval_ms: float
    generation_ms: float
    total_ms: float


class AskResponse(BaseModel):
    request_id: str
    status: Literal["answered", "insufficient_information"]
    answer: str
    sources: list[Source]
    model: Optional[str]
    latency_ms: Latency


class HealthResponse(BaseModel):
    status: Literal["ok"]
    index_ready: bool
    indexed_chunks: int
    llm_backend: str
    llm_model: str
    uptime_s: float


class ErrorResponse(BaseModel):
    error: str
    message: str
    request_id: str
    detail: Optional[list[dict]] = None


# --------------------------------------------------------------------------- application
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    log.info("startup config=%s", settings.public_summary())
    app.state.rag = SupportRAG(settings)          # embedding model + FAISS index loaded ONCE
    app.state.started_at = time.time()
    yield
    app.state.rag.close()
    log.info("shutdown")


app = FastAPI(
    title="NestBasket Customer Support RAG API",
    version="1.0.0",
    description="Answers customer questions from the NestBasket FAQ using retrieval-augmented generation "
                "with a cloud-hosted LLM.",
    lifespan=lifespan,
)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Assigns a request ID, measures response time, and logs one line per request."""
    request.state.request_id = uuid.uuid4().hex[:12]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:  # last line of defence: no stack trace is returned to the client
        log.exception("unhandled_error request_id=%s path=%s", request.state.request_id, request.url.path)
        response = JSONResponse(status_code=500, content=ErrorResponse(
            error="internal_error", message="An unexpected error occurred. Please try again later.",
            request_id=request.state.request_id).model_dump(exclude_none=True))
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
    log.info("request request_id=%s method=%s path=%s status=%d latency_ms=%.1f",
             request.state.request_id, request.method, request.url.path, response.status_code, elapsed_ms)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Invalid input -> 422 with a short, readable reason. The submitted text is not echoed back or logged."""
    detail = []
    for err in exc.errors():
        field = ".".join(p for p in err.get("loc", []) if isinstance(p, str) and p != "body") or "body"
        message = str(err.get("msg", "Invalid value")).removeprefix("Value error, ")
        detail.append({"field": field, "message": message})
    log.warning("validation_failed request_id=%s fields=%s", _request_id(request), [d["field"] for d in detail])
    return JSONResponse(status_code=422, content=ErrorResponse(
        error="invalid_request", message="The request is invalid.", request_id=_request_id(request),
        detail=detail).model_dump(exclude_none=True))


@app.get("/", include_in_schema=False)
def root():
    return {"service": "NestBasket Customer Support RAG API", "docs": "/docs", "health": "/health"}


@app.get("/health", response_model=HealthResponse)
def health(request: Request):
    """Liveness check: the process is running and the vector index is loaded.
    It does not call the LLM provider, so it cannot detect an invalid key or exhausted quota."""
    rag: SupportRAG = request.app.state.rag
    return HealthResponse(
        status="ok",
        index_ready=rag.index_size > 0,
        indexed_chunks=rag.index_size,
        llm_backend=rag.settings.llm_backend,
        llm_model=rag.settings.llm_model,
        uptime_s=round(time.time() - request.app.state.started_at, 1),
    )


@app.post(
    "/ask",
    response_model=AskResponse,
    responses={422: {"model": ErrorResponse, "description": "Invalid input"},
               503: {"model": ErrorResponse, "description": "LLM backend unavailable"}},
)
def ask(payload: AskRequest, request: Request):
    request_id = _request_id(request)
    rag: SupportRAG = request.app.state.rag
    start = time.perf_counter()
    try:
        result = rag.answer(payload.question)
    except BackendError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        log.error("ask_failed request_id=%s error_kind=%s upstream_status=%s question_chars=%d total_ms=%.1f",
                  request_id, exc.kind, exc.upstream_status, len(payload.question), elapsed_ms)
        return JSONResponse(status_code=503, content=ErrorResponse(
            error="backend_unavailable",
            message="The answer service is temporarily unavailable. Please try again later.",
            request_id=request_id).model_dump(exclude_none=True))

    total_ms = (time.perf_counter() - start) * 1000
    top = result.chunks[0] if result.chunks else None
    log.info(
        "ask_ok request_id=%s outcome=%s reason=%s question_chars=%d top_section=%s top_distance=%s "
        "llm_called=%s prompt_tokens=%s completion_tokens=%s retrieval_ms=%.1f generation_ms=%.1f total_ms=%.1f",
        request_id, result.status, result.reason, len(payload.question),
        top.section if top else None, f"{top.distance:.4f}" if top else None, result.llm_called,
        result.prompt_tokens, result.completion_tokens, result.retrieval_ms, result.generation_ms, total_ms,
    )
    return AskResponse(
        request_id=request_id,
        status=result.status,
        answer=result.answer,
        sources=[Source(section=c.section, distance=round(c.distance, 4)) for c in result.chunks],
        model=result.model,
        latency_ms=Latency(retrieval_ms=round(result.retrieval_ms, 1),
                           generation_ms=round(result.generation_ms, 1),
                           total_ms=round(total_ms, 1)),
    )
