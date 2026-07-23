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

Every configuration field maps to an environment variable: uppercase the field
name and add the `EZBAK_` prefix.

```bash
export EZBAK_NAME="my-backup"
export EZBAK_SOURCE_PATHS="/data"
export EZBAK_STORAGE_PATHS="/backups"
export EZBAK_KEEP_DAILY=7
```

So the `source_paths` field is `EZBAK_SOURCE_PATHS`, and `keep_daily` is
`EZBAK_KEEP_DAILY`. A few settings control the container entrypoint and have no
library field or CLI flag, such as `EZBAK_ACTION` and `EZBAK_CRON`; the
[configuration reference](configuration.md#container-only-options) marks them as
container-only.

## .env and .env.secrets files

The container also reads a `.env` and a `.env.secrets` file from its working
directory, so you can keep secrets out of the process environment. A value in the
process environment wins over the same key in a file.

!!! warning "Running the container locally reads your .env files"

    Because the container reads `.env` and `.env.secrets`, running the image on a
    development machine can pick up real S3 credentials. Keep those files out of
    directories you mount into a test container.

## TZ and EZBAK_TZ

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
