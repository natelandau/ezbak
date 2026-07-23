---
icon: lucide/variable
---

# Environment variables

The container reads its whole configuration from the environment. The CLI reads a
few options from the environment too, so credentials and the timezone never have
to pass through command-line flags. This page covers the variables specific to
running the container. Every `BackupConfig` field is also available as an
environment variable, listed in the [configuration reference](configuration.md).

## The EZBAK_ prefix

Every configuration field maps to an environment variable: uppercase the field
name and add the `EZBAK_` prefix.

```bash
export EZBAK_NAME="my-backup"
export EZBAK_SOURCE_PATHS="/data"
export EZBAK_STORAGE_PATHS="/backups"
export EZBAK_KEEP_DAILY=7
```

The container also reads a `.env` and a `.env.secrets` file from its working
directory, so you can keep secrets out of the process environment.

!!! warning "Running the container locally reads your .env files"

    Because the container reads `.env` and `.env.secrets`, running the image on a
    development machine can pick up real S3 credentials. Keep those files out of
    directories you mount into a test container.

## Container-only variables

These control the container entrypoint and have no equivalent library field or
CLI flag.

| Variable | Values | Purpose |
| --- | --- | --- |
| `EZBAK_ACTION` | `backup` or `restore` | The action to run. Required. |
| `EZBAK_CRON` | a cron expression | Run the action on a schedule instead of once. |
| `EZBAK_CRON_JITTER` | seconds (integer) | Random delay added to each scheduled run so a fleet sharing one cron does not hit a destination at once. Default `60`; set `0` to disable. |
| `EZBAK_HEALTHCHECK_URL` | a URL | Ping a monitor after each scheduled run. |
| `EZBAK_BACKUP_ON_SHUTDOWN` | `true` or `false` | Take a final backup when a scheduled backup container shuts down. Default `false`. |
| `EZBAK_PRE_BACKUP_HOOK` | a shell command | Run before the container creates a backup. Unset is a no-op. |
| `EZBAK_POST_BACKUP_HOOK` | a shell command | Run after the container creates a backup. Unset is a no-op. |
| `EZBAK_PRE_RESTORE_HOOK` | a shell command | Run before the container restores a backup. Unset is a no-op. |
| `EZBAK_POST_RESTORE_HOOK` | a shell command | Run after the container restores a backup. Unset is a no-op. |
| `EZBAK_HOOK_TIMEOUT` | seconds (integer) | Kill a hook that runs longer than this. Default `300`; set `0` to disable. |
| `TZ` | an IANA timezone | System timezone for backup timestamps. |

Without `EZBAK_ACTION`, the container logs an error and exits non-zero. Without
`EZBAK_CRON`, the container runs the action once and exits. See
[Running in Docker](../guides/docker.md). For the five hook variables, see
[Container lifecycle hooks](../guides/hooks.md).

## A one-shot backup

Set the action, name, sources, and storage, then run the image. The container
runs one backup and exits.

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

## A scheduled backup

Add `EZBAK_CRON` to keep the container running and back up on a schedule. Set
`TZ` so the cron expression and the timestamps use your timezone.

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
    -e EZBAK_HEALTHCHECK_URL=https://hc-ping.com/your-uuid \
    -e TZ=America/New_York \
    ghcr.io/natelandau/ezbak:latest
```

A scheduled backup run also prunes afterward using the retention options you set,
so old backups do not accumulate.

!!! note "Scheduled runs are jittered"

    ezbak adds a random delay of up to 60 seconds to each scheduled run, so a
    `"0 2 * * *"` job fires at a random moment within 60 seconds after 02:00.
    Set `EZBAK_CRON_JITTER` to widen or disable the spread, and account for it
    when sizing a healthcheck grace period. See
    [Monitoring](../orchestration/monitoring.md).

## A final backup on shutdown

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

## Timezone: TZ and EZBAK_TZ

ezbak stamps each backup with a timestamp. The timezone comes from one of two
places:

- `TZ` sets the container's system timezone. ezbak uses it when no explicit
  timezone is configured. This is the usual way to set the timezone in a
  container. The image ships with `TZ=Etc/UTC`, so an unconfigured container
  stamps timestamps in UTC.
- `EZBAK_TZ` sets ezbak's `tz` field directly and overrides the system
  timezone.

Set one of them so timestamps match your expectations. See
[Backup names](../concepts/backup-names.md) for the timestamp format.

## Restore variables

For a restore action, point the container at a restore directory and, when you
want an older backup, a restore date.

| Variable | Purpose |
| --- | --- |
| `EZBAK_RESTORE_PATH` | Directory a restore writes into. |
| `EZBAK_RESTORE_DATE` | Restore the newest backup at or before this point in time. |
| `EZBAK_RESTORE_IF_EXISTS` | Treat "no backup to restore" as success. |
| `EZBAK_SKIP_RESTORE_IF_POPULATED` | Skip the restore, as success, when the target already contains data. `EZBAK_CLEAN_BEFORE_RESTORE` bypasses this. |
| `EZBAK_CLEAN_BEFORE_RESTORE` | Empty the restore path before extracting. |
| `EZBAK_USE_CHECKSUMS` | Verify the archive against its `.sha256` sidecar on restore. Set `false` to skip verification. Default `true`. |

`EZBAK_RESTORE_DATE` applies only to the restore action. It accepts the same six
formats as the CLI, from `YYYY` to `YYYYMMDDTHHMMSS`. See
[Restore backups](../guides/restore.md).
