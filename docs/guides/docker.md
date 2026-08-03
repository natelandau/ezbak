---
icon: lucide/container
---

# Running in Docker

The container is the main way to run ezbak. It reads its whole configuration from
`EZBAK_`-prefixed environment variables. It runs a backup or a restore, then
either exits or stays up on a schedule. This guide covers the container on its
own. For the sidecar, post-stop, and pre-start setup, see
[the orchestration pattern](../orchestration/index.md).

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

Mount your source and backup directories as volumes. Point the environment
variables at the mount paths inside the container. The examples below mount the
source `:ro`. If you set `EZBAK_SQLITE_PATHS`, delete that flag, because it
requires a writable source (see
[SQLite databases](../concepts/sqlite.md#mount-the-source-read-write)).

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

A scheduled backup prunes after each run with the retention options you set, so
old backups do not accumulate. A scheduled run also spreads its start time by up
to 60 seconds. Many containers that wake at the same cron minute therefore do not
all reach storage at once. To widen or disable that spread, set
`EZBAK_CRON_JITTER` (seconds).

A loaded host, or a paused container, can delay a run. The run still happens if
it starts within five minutes of its scheduled moment. After that, ezbak skips
the run and logs `Skipped backup: missed by more than the grace period`. A
backlog of missed runs collapses into one candidate run, not one run per
interval. That candidate is the most recent scheduled time, and it still has to
fall inside the five-minute window. A container that resumes more than five
minutes after its last scheduled time therefore starts without a backup and waits
for its next scheduled time.

!!! warning "Scheduled failures do not stop the container"

    A scheduled run that fails logs the error and keeps the container running, so
    the next run retries. Set `EZBAK_HEALTHCHECK_URL` so your monitor alerts you
    when a scheduled run fails or stops happening. See
    [Monitoring](../orchestration/monitoring.md).

## Forcing an on-demand backup

A scheduled container waits for its cron time. Send it `SIGUSR1` to run the
configured action immediately, ahead of the schedule.

```bash
docker kill --signal=SIGUSR1 <container>
```

The forced run takes the same path as a scheduled one. It:

- runs the pre-backup hook,
- backs up to every configured storage location,
- prunes under the retention policy,
- runs the post-backup hook, and
- pings `EZBAK_HEALTHCHECK_URL` when that URL is set.

The cron trigger still computes its next run from the original schedule, so a
forced run does not shift the schedule. To make sure that the signal arrived,
look for `Trigger received; running backup now` in the logs.

The signal forces whatever action the container is scheduled for. A scheduled
restore container therefore accepts the same signal and runs its configured
restore.

Under an orchestrator, send the same signal to the ezbak task or container
directly, not to Docker:

```bash
nomad alloc signal -s SIGUSR1 -task <task> <alloc>
kubectl exec <pod> -c <container> -- kill -USR1 1
```

!!! note "Only scheduled containers listen for the signal"

    `SIGUSR1` acts only on a container that runs with `EZBAK_CRON`. A one-shot
    container ignores it and finishes the run in progress. A signal sent to the
    wrong container therefore cannot cut a backup short.

!!! warning "A trigger during a run is refused, not queued"

    A container runs one job at a time. If a `SIGUSR1` arrives while a run is
    already in progress, ezbak refuses it and logs a warning (`Skipped backup: a
    run is already in progress`). It does not queue the signal for after the run.
    If you still want an immediate run, send the signal again after the current
    run completes.

## Final backup on shutdown

A scheduled backup container backs up on its cron interval. A shutdown between
runs therefore loses everything written since the last run. Set
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

The option applies only to a cron backup container. It does nothing for a restore
container or a one-shot run. Neither of those has a schedule to shut down.

If a scheduled or forced run is still in progress when the signal arrives, ezbak
lets that run finish and skips the final backup. It logs `A backup is already
running; skipping the final backup`. The running backup already covers the same
data, and a container runs only one backup at a time.

!!! warning "The final backup runs inside the kill grace period"

    An orchestrator sends `SIGTERM`, waits a grace period, then force-kills the
    container with `SIGKILL`. The final backup has to finish within that window.
    Otherwise the orchestrator cuts it off and the backup is lost. The
    orchestrator holds the allocation alive only for that grace period, and the
    backup extends every shutdown by its own duration.

    A shutdown backup is therefore riskier than a dedicated post-stop task, such
    as the `poststop` lifecycle of Nomad. That task runs as its own step, with
    its own completion window. Prefer it for backups that can run long. Use
    `EZBAK_BACKUP_ON_SHUTDOWN` when the backup sidecar has to stand on its own.
    Size the grace period to cover a backup of your data: the `kill_timeout` of
    Nomad, and the `terminationGracePeriodSeconds` of Kubernetes. See the
    [orchestration examples](../orchestration/index.md).

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

For every option and its `EZBAK_` variable, see the
[configuration reference](../reference/configuration.md). For how ezbak reads
those variables from the environment and from `.env` files, see
[Environment variables](../reference/environment-variables.md).
