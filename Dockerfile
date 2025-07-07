FROM ghcr.io/astral-sh/uv:0.7.19-python3.13-bookworm-slim

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

# Copy the project into the image
ADD . /app

WORKDIR /app
RUN uv sync --no-dev

# Sync the project into a new environment, asserting the lockfile is up to date
# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Reset the entrypoint
ENTRYPOINT []

# Run ezbak
CMD [".venv/bin/python", "-m", "ezbak.entrypoint"]
