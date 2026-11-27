# syntax=docker/dockerfile:1.7

# ============================================================
# Stage 1: Builder - resolve and install Python dependencies
# ============================================================
# Both stages must use the same Python minor version: the runtime stage runs the builder's
# virtualenv against its own interpreter.
FROM ghcr.io/astral-sh/uv:0.13.9-python3.14-trixie-slim AS builder

# UV_PYTHON_DOWNLOADS=never keeps uv on the image's interpreter, the one the runtime
# stage provides, instead of downloading the version .python-version names
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

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
FROM python:3.14-slim-trixie

# Runtime-only system deps. tini gives us proper PID 1 signal handling so
# APScheduler shuts down cleanly on SIGTERM. curl, sqlite3, and tar are for
# operator hook commands and for inspecting a running container; ezbak itself
# uses the stdlib tarfile and sqlite3 modules.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        sqlite3 \
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
LABEL org.opencontainers.image.description="Back up and restore files and directories as tar.gz archives on local disks and S3."
LABEL org.opencontainers.image.licenses=MIT
LABEL org.opencontainers.image.url=https://github.com/natelandau/ezbak
LABEL org.opencontainers.image.title="ezbak"

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "ezbak.container"]
