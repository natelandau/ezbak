FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Set labels
LABEL org.opencontainers.image.source=https://github.com/natelandau/ezbak
LABEL org.opencontainers.image.description="ezbak"
LABEL org.opencontainers.image.licenses=MIT
LABEL org.opencontainers.image.url=https://github.com/natelandau/ezbak
LABEL org.opencontainers.image.title="ezbak"

# Install Apt Packages
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tar tzdata cron

# Set timezone
ENV TZ=Etc/UTC
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ >/etc/timezone

# Install the project into `/app`
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Then, add the rest of the project source code and install it
# Installing separately from its dependencies allows optimal layer caching
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Sync the project into a new environment, asserting the lockfile is up to date
# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Reset the entrypoint
ENTRYPOINT []

# Run ezbak
CMD [".venv/bin/python", "-m", "ezbak.entrypoint"]
