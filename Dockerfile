# =============================================================================
# GraphGuard backend image — serves the API and runs the Celery worker.
# =============================================================================
# Both processes ship in one image because they share the entire codebase: the
# worker runs the detection pipeline, and the API must import the same models
# and schema to serve its results. Two images would double the build time and
# create a class of bug where the API and the worker disagree about the schema.
# They differ only in their command, which docker-compose supplies.
# =============================================================================

# --- Stage 1: dependencies ---------------------------------------------------
FROM python:3.12-slim-bookworm AS deps

# uv resolves and installs from the committed lock file, so the image gets the
# same dependency graph that was tested, not "whatever resolved on build day".
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer, cached independently of source. Editing a Python file must
# not trigger a fresh torch download.
COPY pyproject.toml uv.lock README.md ./
# The assistant extra is included so GRAPHGUARD_ASSISTANT_ENABLED works in a deployed
# stack without an image rebuild; the dependency is inert while the flag is off.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra assistant

# Now the project itself, in its own layer: this is what puts the `graphguard`
# console script into the venv. Without it the CLI the deployment depends on —
# `graphguard platform create-admin` is the only way to bootstrap the first
# administrator (D21) — simply does not exist inside the container.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra assistant

# --- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# libgomp is required by torch at import time; curl backs the healthcheck.
# Installed without recommends and with the apt lists dropped, so the layer
# carries only what runs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# A non-root user. If the API is ever exploited, the attacker lands as an
# unprivileged account that cannot write the application code it is running —
# which for a security product is the minimum bar rather than a nicety.
RUN groupadd --system --gid 1001 graphguard \
    && useradd --system --uid 1001 --gid graphguard --create-home graphguard

WORKDIR /app

COPY --from=deps --chown=graphguard:graphguard /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=graphguard:graphguard pyproject.toml uv.lock README.md alembic.ini ./
COPY --chown=graphguard:graphguard migrations ./migrations
COPY --chown=graphguard:graphguard config ./config
COPY --chown=graphguard:graphguard src ./src

ENV PYTHONPATH=/app/src

USER graphguard

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "graphguard.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
