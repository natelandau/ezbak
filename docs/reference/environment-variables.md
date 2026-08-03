---
icon: lucide/variable
---

# Environment variables

The container reads its whole configuration from the environment. The CLI reads a
few options from the environment too, so credentials and the timezone never have
to pass through command-line flags.

This page explains how ezbak turns environment variables into configuration. For
the options themselves, use the two pages it links to:

- The [configuration reference](configuration.md) lists every option with its
  environment variable, CLI flag, and default.
- [Running in Docker](../guides/docker.md) has runnable `docker run` and Compose
  examples for each container run mode.

## The EZBAK_ prefix

Every configuration field maps to an environment variable. Write the field name
in uppercase and add the `EZBAK_` prefix.

```bash
export EZBAK_NAME="my-backup"
export EZBAK_SOURCE_PATHS="/data"
export EZBAK_STORAGE_PATHS="/backups"
export EZBAK_SQLITE_PATHS="/data/app.db"
export EZBAK_KEEP_DAILY=7
```

The `source_paths` field is therefore `EZBAK_SOURCE_PATHS`, and `keep_daily` is
`EZBAK_KEEP_DAILY`. A field that takes a list of paths reads a comma-separated
string, so `EZBAK_SQLITE_PATHS="/data/app.db,/data/sessions.db"` names two
databases. An entry in `EZBAK_SQLITE_PATHS` can also be a glob pattern, such as
`EZBAK_SQLITE_PATHS=/data/shards/*.db`. See [Match databases with a
pattern](../concepts/sqlite.md#match-databases-with-a-pattern).

A few options control the container entrypoint and have no library field and no
CLI flag, such as `EZBAK_ACTION` and `EZBAK_CRON`. The [configuration
reference](configuration.md#container-only-options) marks them as container-only.

## .env and .env.secrets files

The container also reads a `.env` file and a `.env.secrets` file from its working
directory, so you can keep secrets out of the process environment. A value in the
process environment wins over the same key in a file.

!!! warning "Running the container locally reads your .env files"

    Keep `.env` and `.env.secrets` out of any directory you mount into a test
    container. The container reads both files, so running the image on a
    development machine can load real S3 credentials.

## TZ and EZBAK_TZ

ezbak stamps each backup with a timestamp. The timezone comes from one of two
places:

- `TZ` sets the system timezone of the container. ezbak uses it when no explicit
  timezone is configured. This is the usual way to set the timezone in a
  container. The image ships with `TZ=Etc/UTC`, so an unconfigured container
  stamps timestamps in UTC.
- `EZBAK_TZ` sets the `tz` field of ezbak directly and overrides the system
  timezone.

Set one of the two so the timestamps match your expectations. For the timestamp
format, see [Backup names](../concepts/backup-names.md).
