# Experiment 8 — Measured results (Azure Container Apps)

All values below were measured on 2026-10-31 (UTC) against the deployed service. Nothing is estimated.

## 1. Resources

| Item | Value |
|---|---|
| Subscription | Azure for Students |
| Region | `centralindia` (Central India) — no fallback needed |
| Resource group | `rg-exp7-rag-api` |
| Container registry | `acrexp7rag5973` (`acrexp7rag5973.azurecr.io`), SKU Basic, admin user **disabled** |
| Container Apps environment | `cae-exp7-rag-api`, mode `WorkloadProfiles` (Consumption profile only), domain `politefield-e1a38991.centralindia.azurecontainerapps.io` |
| Log Analytics workspace | `workspace-rgexp7ragapi4Rj0` (the only one) |
| Container app | `exp7-rag-api` |
| Application URL | https://exp7-rag-api.politefield-e1a38991.centralindia.azurecontainerapps.io |

`az resource list -g rg-exp7-rag-api -o table`:

```
Name                        ResourceGroup    Location      Type                                      Status
--------------------------  ---------------  ------------  ----------------------------------------  ---------
acrexp7rag5973              rg-exp7-rag-api  centralindia  Microsoft.ContainerRegistry/registries    Succeeded
workspace-rgexp7ragapi4Rj0  rg-exp7-rag-api  centralindia  Microsoft.OperationalInsights/workspaces  Succeeded
cae-exp7-rag-api            rg-exp7-rag-api  centralindia  Microsoft.App/managedEnvironments         Succeeded
exp7-rag-api                rg-exp7-rag-api  centralindia  Microsoft.App/containerApps               Succeeded
```

## 2. Image

| Item | Value |
|---|---|
| Repository:tag | `acrexp7rag5973.azurecr.io/exp7-rag-api:v1` |
| Index digest | `sha256:0b6e49a39304a458970d0d220580ecc94545f4ce1848d50991951af6bdd51f90` |
| linux/amd64 manifest | `sha256:72fb80613982b9e2f390350915696e351fdbacf9de41f34481e38746b69efa17` |
| Size in registry (compressed) | **584,472,868 bytes (584.5 MB)**, 11 layers |
| Size on local disk (uncompressed) | 2.52 GB |
| `az acr build` | **failed** — `TasksOperationsNotAllowed` (ACR Tasks are blocked on Azure for Students subscriptions) |
| Local `docker build` | **891 s** (the 2 attempts before it failed with pip `ReadTimeoutError` from `download-r2.pytorch.org`; fixed with `PIP_DEFAULT_TIMEOUT=120`) |
| `docker push` (via `az acr login`, Entra ID token) | 1st attempt: failed after 1,295 s (`416 Requested Range Not Satisfiable` on one layer); retry: **490 s** (completed layers were reused) |

## 3. App configuration

| Setting | Value |
|---|---|
| CPU / memory | 1.0 vCPU / 2 GiB |
| Scaling | min 0, max 2 replicas (HTTP scaling, cooldown 300 s, polling 30 s) |
| Ingress | external, target port 8000, HTTPS |
| Registry auth | system-assigned managed identity with `AcrPull` on the registry (`username` and `passwordSecretRef` empty) |
| Secret | `llm-api-key` (Container Apps secret) |
| Env vars | `LLM_BACKEND=openai_compatible`, `LLM_BASE_URL=https://api.groq.com/openai/v1`, `LLM_MODEL=openai/gpt-oss-20b`, `LLM_TIMEOUT_SECONDS=30`, `LLM_MAX_TOKENS=300`, `RAG_TOP_K=3`, `RAG_MAX_DISTANCE=1.5`, `LLM_API_KEY=secretref:llm-api-key` |
| Model | `openai/gpt-oss-20b` worked on Groq (no 404), so no model change was needed |
| Revision mode | Single |
| Provisioning times | express environment 133 s (deleted, see §7); `az containerapp create` 47 s |
| In-container start-up (`rag_ready startup_ms`) | 5,792 / 6,214 / 6,786 / 5,604 ms (revisions 2, 3, 4, and the cold-start replica of 4) |

## 4. Latency

### Cold start and /health

| Measurement | Client | Server (`X-Response-Time-ms`) |
|---|---|---|
| **Cold start** — first `GET /health` with 0 replicas (scaled to zero at 10:19:05 UTC; response `uptime_s` 2.7) | **28.114 s** (`time curl`) | — |
| Warm `GET /health`, 5 calls after the cold start | 47–58 ms | 0.7–1.2 ms |
| Warm `GET /health`, 5 calls on revision 2 | 55–92 ms | 0.8–1.1 ms |

`curl -s -i $URL/health` (warm):

```
HTTP/2 200
server: uvicorn
content-type: application/json
x-request-id: 22d2dc9e0f6f
x-response-time-ms: 1.2

{"status":"ok","index_ready":true,"indexed_chunks":16,"llm_backend":"openai_compatible","llm_model":"openai/gpt-oss-20b","uptime_s":65.5}
```

### POST /ask (curl, warm replica)

| Case | HTTP | `status` / `error` | Server ms | Retrieval ms | Generation ms | Client ms | Request ID |
|---|---|---|---|---|---|---|---|
| "How many days do I have to return an unused item?" | 200 | `answered` — "You have 15 days from the delivery date to return an unused item in its original packaging." | 547.8 | 17.8 | 526.9 | 618.9 | `7ac1c1be2150` |
| "Do you sell car insurance?" | 200 | `insufficient_information` (top distance 1.5089 > 1.5, LLM not called) | 10.2 | 8.7 | 0.0 | 116.9 | `e6d00d518775` |
| `""` (empty) | 422 | `invalid_request` — "Question must not be empty." | 1.0 | — | — | 79.8 | `9d80845d24df` |
| 1,001 × `a` | 422 | `invalid_request` — "Question must be at most 1000 characters (received 1001)." | 1.0 | — | — | 102.9 | `21f6c2a3c98e` |
| "How do I return an item?" with `LLM_API_KEY=invalid-key-for-503-test` (revision 3) | 503 | `backend_unavailable` | 77.2 | — | — | 154.5 | `b6cd0830c06a` |
| Supported question after restoring the secret (revision 4) | 200 | `answered` (15 days) | 848.6 | 82.8 | 756.9 | 961.4 | `aef93da2f9d2` |

Server log for the 503 case: `ask_failed request_id=b6cd0830c06a error_kind=authentication_failed upstream_status=401 question_chars=24 total_ms=74.2`.

## 5. test_api.py against the cloud URL

```
$ python test_api.py https://exp7-rag-api.politefield-e1a38991.centralindia.azurecontainerapps.io
Target: https://exp7-rag-api.politefield-e1a38991.centralindia.azurecontainerapps.io

Test case                          Expected                         Pass   Client ms  Server ms  Actual
Health endpoint                    200, status ok                   PASS       138.8        0.7  200, ok
Supported question                 200, answered                    PASS       717.6      594.4  200, answered: You have 15 days from the delivery date to return an unused item in it
Unsupported question               200, insufficient_information    PASS        88.7        9.3  200, insufficient_information
Empty question                     422 validation error             PASS        89.8        2.0  422, invalid_request
Too-long question (1,001 chars)    422 validation error             PASS        69.9        1.2  422, invalid_request
[exit code 0]
```

**5 / 5 PASS** (full output: `docs/exp8_test_api_cloud.txt`).

## 6. Logs (key-free)

Excerpt from `az containerapp logs show -n exp7-rag-api -g rg-exp7-rag-api --type console --tail 100` (full copy: `docs/exp8_container_logs.txt`):

```
startup config={'llm_backend': 'openai_compatible', ..., 'llm_model': 'openai/gpt-oss-20b', 'api_key_configured': True, ...}
rag_ready sections=13 chunks=16 backend=openai_compatible model=openai/gpt-oss-20b startup_ms=6786
ask_ok request_id=aef93da2f9d2 outcome=answered reason=grounded_answer question_chars=49 top_section=Returns and Refunds top_distance=0.7173 llm_called=True prompt_tokens=468 ...
request request_id=aef93da2f9d2 method=POST path=/ask status=200 latency_ms=848.6
ask_failed request_id=b6cd0830c06a error_kind=authentication_failed upstream_status=401 question_chars=24 total_ms=74.2   (revision 3, from Log Analytics)
```

Key checks: `grep -c gsk_ docs/exp8_container_logs.txt` → **0**; `history | grep -c gsk_` → **0**; `grep -c gsk_ ~/.bash_history` → **0**; no `gsk_` in any repository file.

## 7. Revisions

`az containerapp revision list -n exp7-rag-api -g rg-exp7-rag-api --all -o table`:

```
Name                   Created                    Active    Replicas    Traffic    Health
---------------------  -------------------------  --------  ----------  ---------  --------
exp7-rag-api--nnyoskj  2026-10-31T10:05:22+00:00  False     0           0          Healthy
exp7-rag-api--0000001  2026-10-31T10:05:46+00:00  False     0           0          Healthy
exp7-rag-api--0000002  2026-10-31T10:06:40+00:00  False     0           0          Healthy
exp7-rag-api--0000003  2026-10-31T10:08:59+00:00  False     0           0          Healthy
exp7-rag-api--0000004  2026-10-31T10:10:58+00:00  True      1           100        Healthy
```

| Revision | What it is |
|---|---|
| `--nnyoskj` | placeholder image created internally by `az containerapp create` while it assigns the managed identity |
| `--0000001` | image `v1`, no key yet (`api_key_configured: False`) |
| `--0000002` | `LLM_API_KEY=secretref:llm-api-key` |
| `--0000003` | `LLM_API_KEY=invalid-key-for-503-test` (503 test) |
| `--0000004` | `LLM_API_KEY=secretref:llm-api-key` restored — **active, 100 % traffic** |

### Deviations from the plan, and why

1. **`az acr build` → local `docker build` + `docker push`.** ACR Tasks are not permitted on Azure for Students (`TasksOperationsNotAllowed`). The push used `az acr login`, which uses an Entra ID token, so the registry still has no admin user or password.
2. **Environment recreated in `WorkloadProfiles` mode.** With containerapp extension 1.3.0b5, `az containerapp env create` produced an *Express* environment, which rejects `--registry-identity system` (`ExpressEnvironmentFeatureNotSupported`). The documented fallback (`registry set --identity system`) hits the same restriction. The environment was deleted and recreated with `--environment-mode WorkloadProfiles` (Consumption profile only: serverless, scales to zero, no fixed cost), reusing the same Log Analytics workspace.
3. **`PIP_DEFAULT_TIMEOUT=120` added to the Dockerfile `ENV` line** so the large torch download does not hit pip's 15 s read timeout. The application code is unchanged.
4. **Measurements taken during the revision switch were discarded.** For about 10 s after `secretref` was set, traffic was still served by revision 1 (no key → 503 `configuration_missing_api_key`). All `/ask` numbers above were taken after revision 2 was the only active revision. For the 503 test and the restore, the script waited until the new revision was the only active, healthy revision.

## 8. Screenshots

Captured automatically (`scripts/exp8_screenshots.py`, Playwright 1400×900; a caption bar shows the URL because Playwright does not draw an address bar):

| File | Caption |
|---|---|
| `docs/screenshots/cloud_health.png` | `GET /health` on the cloud URL — `index_ready: true`, `indexed_chunks: 16` |
| `docs/screenshots/cloud_swagger_docs.png` | Swagger UI (`/docs`) served from Azure Container Apps |
| `docs/screenshots/cloud_swagger_ask_200.png` | `POST /ask` supported question → 200 `answered` (15 days), with headers |
| `docs/screenshots/cloud_swagger_ask_unsupported.png` | `POST /ask` "Do you sell car insurance?" → 200 `insufficient_information` |
| `docs/screenshots/cloud_swagger_ask_422.png` | `POST /ask` empty question → 422 `invalid_request` |
| `docs/screenshots/cloud_swagger_ask_503.png` | `POST /ask` on the wrong-key revision → 503 `backend_unavailable` |
| `docs/screenshots/terminal_az_deploy.png` | Deployment commands and outputs (key masked) |
| `docs/screenshots/terminal_test_api_cloud.png` | `python test_api.py $URL` — 5/5 PASS |
| `docs/screenshots/terminal_az_show.png` | `az containerapp show`, revision lists, ACR tags, resource list |
| `docs/screenshots/terminal_az_logs.png` | Container console logs (no key) |
| `docs/screenshots/github_readme_exp8.png` | GitHub README showing the Experiment 8 deployment section |

To be added manually from the Azure Portal (login required):

| File (suggested) | Caption |
|---|---|
| `portal_resource_group.png` | Resource group `rg-exp7-rag-api` overview with the 4 resources |
| `portal_containerapp_overview.png` | Container app overview — Application URL, status Running, region Central India |
| `portal_containerapp_revisions.png` | Revisions and replicas — revision 0000004 active, 100 % traffic |
| `portal_containerapp_secrets.png` | Settings → Secrets — `llm-api-key` (value hidden) |
| `portal_containerapp_env_vars.png` | Containers → Environment variables — `LLM_API_KEY` = secret reference |
| `portal_containerapp_scale.png` | Scale rule settings — min 0 / max 2 replicas |
| `portal_containerapp_identity.png` | Identity — system-assigned managed identity enabled |
| `portal_acr_repository.png` | Container registry → Repositories → `exp7-rag-api:v1` |
| `portal_acr_access_keys.png` | Container registry → Access keys — admin user disabled |
| `portal_acr_role_assignment.png` | Registry IAM — `AcrPull` assigned to the container app identity |
| `portal_log_analytics_query.png` | Log Analytics → `ContainerAppConsoleLogs_CL` query showing `ask_ok` / `ask_failed` lines |
| `portal_metrics_requests.png` | Container app → Metrics — Requests / Replica count over the test window |
| `portal_cost_analysis.png` | Cost Management → Cost analysis for the resource group |
