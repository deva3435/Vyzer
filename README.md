# Vyzer

Vyzer is a Streamlit AI assistant with local Ollama model routing, FastAPI authentication/chat persistence, document/web grounding, conversation memory, and authoritative executable-code verification.

## Architecture

```text
Browser
  |
  v
Streamlit :8501
  |-----------------------> Ollama :11434
  |
  +-----------------------> FastAPI :8000 ----> SQLite (development)
                                      |
                                      +--------> PostgreSQL (production)
```

The executable verifier is local to the Streamlit process. It is **not a security sandbox** and must not be exposed to arbitrary untrusted public code without an external isolation layer.

## Requirements

- Python 3.11 or 3.12 (3.12 is the deployment target)
- Ollama with at least one supported model
- FastAPI backend for authentication/history
- C++/C/Java/Node/TypeScript/Go/Rust toolchains only when those languages are being verified

## Local Windows startup

### Terminal 1 — Ollama

Start Ollama normally. Confirm it answers at `http://localhost:11434/api/tags` and install the models you want to use.

Optional routing configuration:

```text
VYZER_GENERAL_MODEL=gemma3:4b
VYZER_CODER_MODEL=qwen2.5-coder:7b
VYZER_REASONING_MODEL=deepseek-r1:7b
VYZER_VISION_MODEL=qwen2.5vl:7b
```

Leave these blank for capability-based discovery.

### Terminal 2 — FastAPI

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:ENVIRONMENT="development"
$env:DATABASE_URL="sqlite:///./vyzer.db"
$env:JWT_SECRET_KEY="replace-with-a-long-development-secret"
$env:CORS_ORIGINS="http://localhost:8501"
python scripts/upgrade_db.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Health check: `http://127.0.0.1:8000/health`

### Terminal 3 — Streamlit

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:BACKEND_URL="http://127.0.0.1:8000"
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:AUTO_VERIFY_CODE="true"
python -m streamlit run app.py --server.port 8501
```

Or use `scripts/start_backend.ps1` and `scripts/start_frontend.ps1`.

## Environment

Copy `.env.example` to `.env` for local configuration. Never commit `.env`.

Backend production configuration is separate in `backend/.env.example`. Production requires a strong `JWT_SECRET_KEY`, explicit CORS origins, and a non-local `DATABASE_URL`.

## Verification architecture

Executable verification is fail-closed:

- `NOT_APPLICABLE` — no complete executable candidate
- `GENERATED` — response exists but no verification was performed
- `COMPILED` — code compiled/syntax-checked but no concrete input was available
- `EXECUTED` — code ran with supplied input, but correctness was not established
- `VERIFIED` — supplied input was executed and actual stdout matched the original expected output exactly after the project's comparison normalization
- `FAILED` — execution, compilation, repair, or verification failed
- `ENVIRONMENT_UNAVAILABLE` — the required toolchain itself is unavailable

`VERIFIED` can only be produced by `CodeVerifier` after execution with the original input and exact expected-output comparison. UI code consumes the structured state; it never infers verification from prose such as “verified” or “successfully”.

Automatic repair is constrained by immutable original test input and expected output. Failed repairs preserve the original answer.

## Output rules

When an explicit expected output is supplied, generation and repair prompts instruct coding models to emit judge-style output without interactive prompts or explanatory labels. The verifier still compares actual stdout directly; it does not strip prompts or perform fuzzy matching.

## Model routing

`ModelRouter` makes one capability-based decision per request:

- coding → configured/discovered coder model
- hard reasoning → configured/discovered reasoning model
- image → vision model, then answer capability
- general → configured/discovered general model

The selected model is recorded with each assistant message and the UI badge shows the last model actually used. If the required capability is unavailable, Vyzer reports the problem instead of silently substituting an unrelated model.

## Database/authentication

FastAPI provides:

- signup/login with Argon2 password hashing
- JWT bearer authentication
- per-user conversation ownership
- message ownership through conversations
- SQLite development support
- PostgreSQL production support
- Alembic migrations
- configurable CORS
- basic per-process authentication rate limiting

Production password recovery is intentionally not implemented by the legacy direct-reset endpoint. A real deployment should connect an email-verified recovery flow before enabling password recovery for external users.

## Tests

Install development dependencies:

```powershell
python -m pip install -r requirements-dev.txt
```

Run the complete regression suite:

```powershell
python -m pytest -q
```

Run syntax checks:

```powershell
python -m compileall -q .
```

The regression suite covers verification states, exact output matching, prompt/label contamination, repair, fragment rejection, compiler diagnostics, all supported language runners where toolchains are available, timeouts, model routing, application-level verification decisions, and backend authentication/conversation ownership.

## Production deployment

Build and run the backend container from the repository root:

```powershell
docker build -f backend/Dockerfile .
```

For a local PostgreSQL-backed deployment, `docker compose up --build` can start PostgreSQL, FastAPI, and Streamlit. Ollama remains an external service and must be reachable from the frontend container.

Before production deployment:

1. Use PostgreSQL or another supported server database.
2. Set a strong random `JWT_SECRET_KEY`.
3. Configure explicit `CORS_ORIGINS`.
4. Run `python scripts/upgrade_db.py` as the deployment migration step; it preserves legacy local databases that predate Alembic.
5. Put Streamlit/FastAPI behind TLS and an appropriate reverse proxy.
6. Do not expose the local verifier to arbitrary public users without OS/container/VM isolation.
7. Give the code runner a dedicated low-privilege execution environment with no application secrets, no database credentials, restricted filesystem access, restricted network access, CPU/memory limits, and process isolation.

## Security limitation of local verification

`TemporaryDirectory`, timeouts, and a restricted child environment reduce accidental damage but are **not a sandbox**. Generated code can still exploit kernel/runtime vulnerabilities or resources available to the local user. Production public execution therefore requires a real isolation boundary such as a dedicated container/VM/job runner with seccomp/AppContainer/job-object/resource/network controls appropriate to the deployment platform.
