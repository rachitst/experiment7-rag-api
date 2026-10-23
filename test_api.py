"""Endpoint test client for the running API (Section 8 testing checklist).

Usage (with the server already running in another terminal):
    python test_api.py                       # tests http://127.0.0.1:8000
    python test_api.py http://127.0.0.1:8001 # tests another address

The backend-failure test needs a second server started with a wrong key; see README.md.
"""
import sys
import time

import httpx

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


def call(method: str, path: str, **kwargs):
    start = time.perf_counter()
    response = httpx.request(method, BASE_URL + path, timeout=90, **kwargs)
    client_ms = (time.perf_counter() - start) * 1000
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:200]}
    return response.status_code, body, client_ms, response.headers.get("X-Response-Time-ms")


def main() -> int:
    results = []

    status, body, ms, server_ms = call("GET", "/health")
    results.append(("Health endpoint", "200, status ok", f"{status}, {body.get('status')}",
                    status == 200 and body.get("status") == "ok", ms, server_ms))

    status, body, ms, server_ms = call("POST", "/ask", json={"question": "How many days do I have to return an unused item?"})
    results.append(("Supported question", "200, answered", f"{status}, {body.get('status')}: {str(body.get('answer'))[:70]}",
                    status == 200 and body.get("status") == "answered", ms, server_ms))

    status, body, ms, server_ms = call("POST", "/ask", json={"question": "Do you sell home insurance?"})
    results.append(("Unsupported question", "200, insufficient_information", f"{status}, {body.get('status')}",
                    status == 200 and body.get("status") == "insufficient_information", ms, server_ms))

    status, body, ms, server_ms = call("POST", "/ask", json={"question": "   "})
    results.append(("Empty question", "422 validation error", f"{status}, {body.get('error')}",
                    status == 422, ms, server_ms))

    status, body, ms, server_ms = call("POST", "/ask", json={"question": "a" * 1001})
    results.append(("Too-long question (1,001 chars)", "422 validation error", f"{status}, {body.get('error')}",
                    status == 422, ms, server_ms))

    print(f"Target: {BASE_URL}\n")
    print(f"{'Test case':34s} {'Expected':32s} {'Pass':5s} {'Client ms':>10s} {'Server ms':>10s}  Actual")
    for name, expected, actual, ok, client_ms, srv in results:
        print(f"{name:34s} {expected:32s} {'PASS' if ok else 'FAIL':5s} {client_ms:10.1f} {str(srv):>10s}  {actual}")
    return 0 if all(r[3] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
