# Use a Python image with uv pre-installed
FROM python:3.12-slim-bookworm

# Set labels
LABEL org.opencontainers.image.source=https://github.com/natelandau/ezbak
LABEL org.opencontainers.image.description="ezbak"
LABEL org.opencontainers.image.licenses=MIT
LABEL org.opencontainers.image.url=https://github.com/natelandau/ezbak
LABEL org.opencontainers.image.title="ezbak"

# Install Apt Packages
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tar tzdata postgresql-client cron

COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /uvx /bin/

# Set timezone
ENV TZ=Etc/UTC
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ >/etc/timezone

# Copy the project into the image
ADD . /app

# Sync the project into a new environment, asserting the lockfile is up to date
WORKDIR /app
RUN uv sync --no-dev

# Reset the entrypoint
ENTRYPOINT []

# Run ezbak
CMD [".venv/bin/python", "-m", "ezbak.entrypoint"]
