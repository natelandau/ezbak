---
icon: lucide/container
---

# Running in Docker

The container is the main way to run ezbak. It reads its whole configuration from
`EZBAK_`-prefixed environment variables, runs a backup or restore, and either
exits or stays up on a schedule. This guide covers the container on its own; for
the sidecar, post-stop, and pre-start setup, see [the orchestration
pattern](../orchestration/index.md).

## The image

Pull the image from the GitHub Container Registry:

```bash
docker pull ghcr.io/natelandau/ezbak:latest
```

## Two required choices

Every container run needs two things: an action and a storage location.

- `EZBAK_ACTION` is `backup` or `restore`. Without it, the container exits
  non-zero.
- A storage location, set with `EZBAK_STORAGE_PATHS`, `EZBAK_AWS_S3_BUCKET_NAME`,
  or both.

Mount your source and backup directories as volumes, and point the environment
variables at the mount paths inside the container.

## One-shot backup

Without `EZBAK_CRON`, the container runs the action once and exits. Use this for
a manual backup or a post-stop task.

```bash
docker run -it \
    -v /path/to/source:/source:ro \
    -v /path/to/backups:/backups \
    -e EZBAK_ACTION=backup \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_SOURCE_PATHS=/source \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_KEEP_LAST=7 \
    ghcr.io/natelandau/ezbak:latest
```

## Scheduled backup

Add `EZBAK_CRON` to keep the container running and back up on a schedule. Set
`TZ` so the schedule and the timestamps use your timezone.

```bash
docker run -d \
    --name ezbak-scheduled \
    --restart unless-stopped \
    -v /path/to/source:/source:ro \
    -v /path/to/backups:/backups \
    -e EZBAK_ACTION=backup \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_SOURCE_PATHS=/source \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_KEEP_LAST=7 \
    -e EZBAK_CRON="0 2 * * *" \
    -e TZ=America/New_York \
    ghcr.io/natelandau/ezbak:latest
```

A scheduled backup prunes after each run using the retention options you set, so
old backups do not build up. A scheduled run also spreads its start time by up to
60 seconds, so many containers waking at the same cron minute do not all hit
storage at once. Widen or disable that spread with `EZBAK_CRON_JITTER` (seconds).

!!! warning "Scheduled failures do not stop the container"

    A scheduled run that fails logs the error and keeps the container running, so
    the next run retries. Set `EZBAK_HEALTHCHECK_URL` to get alerted when a
    scheduled run fails or stops happening. See [Monitoring](../orchestration/monitoring.md).

## Final backup on shutdown

A scheduled backup container backs up on its cron interval, so a shutdown between
runs loses everything written since the last run. Set
`EZBAK_BACKUP_ON_SHUTDOWN=true` to take one final backup when the container
receives `SIGTERM` or `SIGINT`. This caps the loss at a single interval.

```bash
docker run -d \
    --name ezbak-scheduled \
    -v /path/to/source:/source:ro \
    -v /path/to/backups:/backups \
    -e EZBAK_ACTION=backup \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_SOURCE_PATHS=/source \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_CRON="0 2 * * *" \
    -e EZBAK_BACKUP_ON_SHUTDOWN=true \
    ghcr.io/natelandau/ezbak:latest
```

The flag applies only to a cron backup container. It does nothing for a restore
container or a one-shot run, neither of which has a schedule to shut down.

!!! warning "The final backup runs inside the kill grace period"

    An orchestrator sends `SIGTERM`, waits a grace period, then force-kills the
    container with `SIGKILL`. The final backup must finish within that window, or
    it is cut off and lost. The orchestrator holds the allocation alive only for
    that grace period, and the backup extends every shutdown by however long it
    runs.

    A shutdown backup is therefore riskier than a dedicated post-stop task, such
    as Nomad's `poststop` lifecycle, which runs as its own step with its own
    completion window. Prefer that for backups that can run long; reach for
    `EZBAK_BACKUP_ON_SHUTDOWN` when the backup sidecar needs to stand on its own.
    Size the grace period to cover a backup of your data: Nomad's `kill_timeout`
    and Kubernetes' `terminationGracePeriodSeconds`. See the [orchestration
    examples](../orchestration/index.md).

## Restore

Set `EZBAK_ACTION=restore` and a restore path. The container restores the latest
backup unless you name a point in time.

```bash
docker run -it \
    -v /path/to/backups:/backups:ro \
    -v /path/to/restore:/restore \
    -e EZBAK_ACTION=restore \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_RESTORE_PATH=/restore \
    ghcr.io/natelandau/ezbak:latest
```

To restore an older backup, add `EZBAK_RESTORE_DATE`. See
[Restore backups](restore.md).

## Docker Compose

The same configuration works in a Compose file:

```yaml title="compose.yml"
services:
  ezbak:
    image: ghcr.io/natelandau/ezbak:latest
    restart: unless-stopped
    volumes:
      - /path/to/source:/source:ro
      - /path/to/backups:/backups
    environment:
      EZBAK_ACTION: backup
      EZBAK_NAME: my-backup
      EZBAK_SOURCE_PATHS: /source
      EZBAK_STORAGE_PATHS: /backups
      EZBAK_KEEP_LAST: 7
      EZBAK_CRON: "0 2 * * *"
      TZ: America/New_York
```

For every option and its `EZBAK_` variable, see the [configuration
reference](../reference/configuration.md). For how those variables are read from
the environment and `.env` files, see [Environment
variables](../reference/environment-variables.md).
