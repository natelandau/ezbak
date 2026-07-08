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
export EZBAK_RETENTION_DAILY=7
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
| `EZBAK_HEALTHCHECK_URL` | a URL | Ping a monitor after each scheduled run. |
| `TZ` | an IANA timezone | System timezone for backup timestamps. |

Without `EZBAK_ACTION`, the container logs an error and exits non-zero. Without
`EZBAK_CRON`, the container runs the action once and exits. See
[Running in Docker](../guides/docker.md).

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
    -e EZBAK_MAX_BACKUPS=7 \
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
    -e EZBAK_MAX_BACKUPS=7 \
    -e EZBAK_CRON="0 2 * * *" \
    -e EZBAK_HEALTHCHECK_URL=https://hc-ping.com/your-uuid \
    -e TZ=America/New_York \
    ghcr.io/natelandau/ezbak:latest
```

A scheduled backup run also prunes afterward using the retention options you set,
so old backups do not accumulate.

## Timezone: TZ and EZBAK_TZ

ezbak stamps each backup with a timestamp. The timezone comes from one of two
places:

- `TZ` sets the container's system timezone. ezbak uses it when no explicit
  timezone is configured. This is the usual way to set the timezone in a
  container.
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
| `EZBAK_CLEAN_BEFORE_RESTORE` | Empty the restore path before extracting. |

`EZBAK_RESTORE_DATE` applies only to the restore action. It accepts the same six
formats as the CLI, from `YYYY` to `YYYYMMDDTHHMMSS`. See
[Restore backups](../guides/restore.md).
