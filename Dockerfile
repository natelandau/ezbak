# syntax=docker/dockerfile:1.7

# ============================================================
# Stage 1: Builder - resolve and install Python dependencies
# ============================================================
FROM ghcr.io/astral-sh/uv:0.11.23-python3.13-trixie-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies separately from the project so this layer caches
# unless uv.lock or pyproject.toml change.
COPY uv.lock pyproject.toml README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Install the project itself
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ============================================================
# Stage 2: Runtime - lean production image
# ============================================================
FROM python:3.13-slim-trixie

# Runtime-only system deps. tini gives us proper PID 1 signal handling so
# APScheduler shuts down cleanly on SIGTERM.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tar \
        tini \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Etc/UTC
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ >/etc/timezone

WORKDIR /app

# Copy the built virtualenv and project source from the builder stage
COPY --from=builder /app/.venv ./.venv
COPY src/ ./src/

ENV PATH="/app/.venv/bin:$PATH"

# OCI labels last so editing them doesn't bust the build cache
LABEL org.opencontainers.image.source=https://github.com/natelandau/ezbak
LABEL org.opencontainers.image.description="ezbak"
LABEL org.opencontainers.image.licenses=MIT
LABEL org.opencontainers.image.url=https://github.com/natelandau/ezbak
LABEL org.opencontainers.image.title="ezbak"

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "ezbak.container"]
