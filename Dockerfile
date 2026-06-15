# syntax=docker/dockerfile:1

# ---------- Stage 1: build dependencies với uv ----------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    GOOGLE_GENAI_USE_VERTEXAI=0 \
    # run_adk.py đọc SESSION_SERVICE_URI (sync psycopg2) rồi tự derive asyncpg cho ADK 2.0.
    SESSION_SERVICE_URI="postgresql+psycopg2://adk2:adk2@postgres:5432/adk2_session_db" \
    # run_adk.py là click CLI với auto_envvar_prefix="CLI" → đọc host/port/web từ CLI_*.
    CLI_HOST="0.0.0.0" \
    CLI_PORT="8789" \
    CLI_WEB="true"

# libpq5 cho psycopg2-binary
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# Full app (root_agent -> cng -> cng_product) + entrypoint thật run_adk.py + scripts (setup_rag).
COPY --chown=app:app mmvn_b2c_agent ./mmvn_b2c_agent
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app run_adk.py ./run_adk.py
# data/documents cho RAG ingest; data/ cho file runtime
RUN mkdir -p /app/data/documents && chown -R app:app /app/data

USER app
EXPOSE 8789

# Entrypoint thật (như prod): run_adk.py tự tạo bảng (migrations + init_dashboard_auth)
# + mount full api + scheduler + setup_rag. Đọc host/port/web từ CLI_* env, DB từ postgres thật.
CMD ["python", "run_adk.py"]
