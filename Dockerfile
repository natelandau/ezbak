FROM ghcr.io/astral-sh/uv:0.9.18-python3.13-bookworm-slim

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

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Set the working directory
WORKDIR /app

# Copy the project into the image
COPY uv.lock pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN uv sync --locked --no-dev --no-cache

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Reset the entrypoint, don't invoke `uv`
ENTRYPOINT []

# Run ezbak
CMD [".venv/bin/python", "-m", "ezbak.entrypoint"]
