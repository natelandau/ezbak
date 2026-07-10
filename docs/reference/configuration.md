---
icon: lucide/settings
---

# Configuration reference

ezbak takes the same options three ways: as `EZBAK_` environment variables (the
container), as command-line flags, or as arguments to the Python library's
`BackupConfig`. Each table below gives an option's library field, environment
variable, CLI flag, and default, so you never have to translate between surfaces.

The environment variable is the field name uppercased with an `EZBAK_` prefix, so
`source_paths` becomes `EZBAK_SOURCE_PATHS`. CLI flags use their own names, which
do not always match, and some sit on a subcommand such as `create` or `prune`.

A few things to know before the tables:

- Credentials and a couple of other options are read only from the environment,
  with no CLI flag (`aws_access_key`, `aws_secret_key`, `tz`). This keeps
  credentials out of your shell history.
- Some options apply only to the container (`cron`, `EZBAK_ACTION`,
  `healthcheck_url`). See the [environment variables](environment-variables.md)
  reference.
- At least one storage location is required: set `storage_paths`,
  `aws_s3_bucket_name`, or both.

## Identity and sources

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `name` | `EZBAK_NAME` | `-n`, `--name` | required |
| `source_paths` | `EZBAK_SOURCE_PATHS` | `create --source` | none |

`name` identifies the backup set and groups its files. `source_paths` lists the
files and directories to archive. Pass multiple sources by repeating `--source`
on the command line, or as a comma-separated string in the environment variable.

## Storage

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `storage_paths` | `EZBAK_STORAGE_PATHS` | `--storage` | none |
| `aws_s3_bucket_name` | `EZBAK_AWS_S3_BUCKET_NAME` | `--s3-bucket` | `None` |
| `aws_s3_bucket_prefix` | `EZBAK_AWS_S3_BUCKET_PREFIX` | `--s3-bucket-prefix` | `None` |
| `aws_region` | `EZBAK_AWS_REGION` | `--s3-region` | `None` |
| `aws_s3_endpoint_url` | `EZBAK_AWS_S3_ENDPOINT_URL` | `--s3-endpoint-url` | `None` |
| `aws_access_key` | `EZBAK_AWS_ACCESS_KEY` | environment only | `None` |
| `aws_secret_key` | `EZBAK_AWS_SECRET_KEY` | environment only | `None` |

The storage locations you set decide where backups go. There is no
storage-type selector. See [Storage locations](../concepts/storage-locations.md)
for the model and [Back up to S3](../guides/s3.md) for the S3 setup.

## Backup behavior

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `compression_level` | `EZBAK_COMPRESSION_LEVEL` | `create -c`, `--compression-level` | `6` |
| `strip_source_paths` | `EZBAK_STRIP_SOURCE_PATHS` | `create -s`, `--strip-source-paths` | `False` |
| `delete_source_after_backup` | `EZBAK_DELETE_SOURCE_AFTER_BACKUP` | environment only | `False` |
| `include_regex` | `EZBAK_INCLUDE_REGEX` | `create -i`, `--include-regex` | `None` |
| `exclude_regex` | `EZBAK_EXCLUDE_REGEX` | `create -e`, `--exclude-regex` | `None` |
| `use_checksums` | `EZBAK_USE_CHECKSUMS` | `create`/`restore` `--use-checksums/--no-use-checksums` | `True` |

`compression_level` is the gzip level from 1 to 9. `strip_source_paths` flattens
a directory source so `/source/foo.txt` archives as `foo.txt` instead of
`source/foo.txt`. `delete_source_after_backup` removes the sources after a fully
successful backup, and never when any storage location failed. See
[Including and excluding files](../concepts/filtering.md) for the regex options.

`use_checksums` is the master switch for the `.sha256` sidecar feature. With it
enabled, ezbak writes a sidecar next to each new backup archive (for example
`my-documents-20241215T143022.tgz.sha256` alongside
`my-documents-20241215T143022.tgz`) and verifies an archive against its sidecar
on restore. The sidecar uses the same text format as `sha256sum`, so
`sha256sum -c` verifies it too. Set `use_checksums` to `false` and ezbak writes
no new sidecars and skips verification on restore, ignoring any sidecar already
in storage. See [Archive integrity checksums](../concepts/checksums.md).

!!! warning "delete_source_after_backup removes your source data"

    ezbak deletes the sources only after every configured storage location
    confirms a successful write. An S3-only run with bad credentials fails
    before this step, so it never deletes the only copy of your data. Still,
    treat this option with care.

## Retention

Each retention field sets one keep rule. A backup survives pruning if any set
rule marks it, so the rules compose instead of picking one policy.

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `keep_last` | `EZBAK_KEEP_LAST` | `prune --keep-last` | `None` |
| `keep_yearly` | `EZBAK_KEEP_YEARLY` | `prune -Y`, `--keep-yearly` | `None` |
| `keep_monthly` | `EZBAK_KEEP_MONTHLY` | `prune -M`, `--keep-monthly` | `None` |
| `keep_weekly` | `EZBAK_KEEP_WEEKLY` | `prune -W`, `--keep-weekly` | `None` |
| `keep_daily` | `EZBAK_KEEP_DAILY` | `prune -D`, `--keep-daily` | `None` |
| `keep_hourly` | `EZBAK_KEEP_HOURLY` | `prune -H`, `--keep-hourly` | `None` |
| `keep_minutely` | `EZBAK_KEEP_MINUTELY` | `prune -S`, `--keep-minutely` | `None` |

With no rule set, ezbak keeps every backup. Leaving a rule unset, or setting it
to `0`, marks nothing for that rule. See [Retention policies](../concepts/retention.md).

## Restore

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `restore_path` | `EZBAK_RESTORE_PATH` | `restore -d`, `--restore-path` | `None` |
| `restore_date` | `EZBAK_RESTORE_DATE` | `restore -t`, `--restore-date` | `None` |
| `clean_before_restore` | `EZBAK_CLEAN_BEFORE_RESTORE` | `restore --clean-before-restore` | `False` |
| `restore_if_exists` | `EZBAK_RESTORE_IF_EXISTS` | `restore --if-exists` | `False` |
| `chown_uid` | `EZBAK_CHOWN_UID` | `restore -u`, `--uid` | `None` |
| `chown_gid` | `EZBAK_CHOWN_GID` | `restore -g`, `--gid` | `None` |

`restore_date` selects the newest backup at or before a point in time.
`clean_before_restore` empties the target as part of the restore, after a
successful extract, and refuses to target a storage location. `restore_if_exists`
turns a missing backup into a clean no-op instead of a failure. `chown_uid` and
`chown_gid` set ownership on restored files, and both must be set together. See
[Restore backups](../guides/restore.md).

!!! note "restore_if_exists is for the CLI and container"

    A library caller does not need `restore_if_exists`. `restore_backup()`
    returns `False` when there is nothing to restore, so the caller decides how
    to react. The setting exists so the CLI and container can turn that same
    "nothing to restore" result into a zero exit code. See
    [Fresh deploys](../orchestration/fresh-deploys.md).

## Scheduling and timezone

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `cron` | `EZBAK_CRON` | container only | `None` |
| `tz` | `EZBAK_TZ` | environment only | `None` |

`cron` turns the container into a scheduled service. `tz` sets the timezone for
backup timestamps. When `tz` is unset, ezbak uses the system timezone, which the
`TZ` environment variable controls inside a container. See
[Environment variables](environment-variables.md).

## Logging

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `log_level` | `EZBAK_LOG_LEVEL` | `-v`, `-vv` | `INFO` |
| `log_file` | `EZBAK_LOG_FILE` | `--log-file` | `None` |
| `log_prefix` | `EZBAK_LOG_PREFIX` | `--log-prefix` | `None` |

`log_level` accepts `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. On the CLI,
`-v` raises the level to `DEBUG` and `-vv` to `TRACE`. `log_file` also writes
logs to a file. `log_prefix` adds a prefix to every log line, which helps when
several ezbak tasks share one log stream.

## Container-only options

These live on the container adapter, not on the library `BackupConfig`. They
have no CLI flag.

| Setting | Environment variable | Default |
| --- | --- | --- |
| Action | `EZBAK_ACTION` | none |
| Healthcheck URL | `EZBAK_HEALTHCHECK_URL` | `None` |
| Backup on shutdown | `EZBAK_BACKUP_ON_SHUTDOWN` | `false` |
| Pre-backup hook | `EZBAK_PRE_BACKUP_HOOK` | `None` |
| Post-backup hook | `EZBAK_POST_BACKUP_HOOK` | `None` |
| Pre-restore hook | `EZBAK_PRE_RESTORE_HOOK` | `None` |
| Post-restore hook | `EZBAK_POST_RESTORE_HOOK` | `None` |
| Hook timeout | `EZBAK_HOOK_TIMEOUT` | `300` |

`EZBAK_ACTION` is `backup` or `restore` and is required to run the container.
`EZBAK_HEALTHCHECK_URL` pings a monitor after each scheduled run. See
[Monitoring](../orchestration/monitoring.md). `EZBAK_BACKUP_ON_SHUTDOWN` takes
one final backup when a cron backup container receives `SIGTERM` or `SIGINT`;
see [Environment variables](environment-variables.md). The four hook variables
run a shell command before or after a container backup or restore, and
`EZBAK_HOOK_TIMEOUT` bounds how long a hook may run; see [Container lifecycle
hooks](../guides/hooks.md).

*[gzip]: GNU zip compression
