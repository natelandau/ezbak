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
variables at the mount paths inside the container. The examples below mount the
source `:ro`, so drop that flag if you set `EZBAK_SQLITE_PATHS`, which requires a
writable source (see [SQLite databases](../concepts/sqlite.md#mount-the-source-read-write)).

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

A run that cannot start on time, because the host is loaded or the container was
paused, still runs if it gets going within five minutes of its scheduled moment.
Past that, the run is skipped and logged as `Skipped backup: missed by more than
the grace period`. A backlog of missed runs collapses into one candidate run
rather than one run per interval, and that candidate is the most recent scheduled
time, which still has to fall inside the five-minute window. A container that
resumes more than five minutes after its last scheduled time therefore comes back
without backing up and waits for its next scheduled time.

!!! warning "Scheduled failures do not stop the container"

    A scheduled run that fails logs the error and keeps the container running, so
    the next run retries. Set `EZBAK_HEALTHCHECK_URL` to get alerted when a
    scheduled run fails or stops happening. See [Monitoring](../orchestration/monitoring.md).

## Forcing an on-demand backup

A scheduled container waits for its cron time. Send it `SIGUSR1` to run the
configured action right now, without waiting for the schedule.

```bash
docker kill --signal=SIGUSR1 <container>
```

The forced run takes the same path as a scheduled one: it runs the pre-backup
hook, backs up to every configured destination, prunes under the retention
policy, runs the post-backup hook, and pings `EZBAK_HEALTHCHECK_URL` if set.
The cron trigger still computes its next run from the original schedule
afterward, so forcing a run does not shift it. Look for `Trigger received;
running backup now` in the logs to confirm the signal arrived.

The signal forces whichever action the container is scheduled for, so a
scheduled restore container is triggerable the same way and runs its configured
restore.

The same signal works under an orchestrator, sent to the ezbak task or
container directly rather than to Docker:

```bash
nomad alloc signal -s SIGUSR1 -task <task> <alloc>
kubectl exec <pod> -c <container> -- kill -USR1 1
```

!!! note "Only scheduled containers listen for the signal"

    `SIGUSR1` only does something on a container running with `EZBAK_CRON`. A
    one-shot container ignores it and finishes the run it is already doing, so
    signaling the wrong container cannot cut a backup short.

!!! warning "A trigger during a run is refused, not queued"

    A container only runs one job at a time. A `SIGUSR1` that arrives while a
    run is already in progress is refused and logged as a warning (`Skipped
    backup: a run is already in progress`), not queued for after the run
    finishes. Send the signal again once the current run completes if you
    still want an immediate one.

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

If a scheduled or forced run is still in progress when the signal arrives, that
run is left to finish and the final backup is skipped, logged as `A backup is
already running; skipping the final backup`. The running backup already covers
the same data, and a container runs only one backup at a time.

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
