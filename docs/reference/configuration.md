---
icon: lucide/settings
---

# Configuration reference

ezbak takes the same options three ways: as `EZBAK_` environment variables (the
container), as command-line flags, or as arguments to `BackupConfig` in the
Python library. Each table below gives the library field, environment variable,
CLI flag, and default of an option. You therefore never have to translate
between the three interfaces. Every ezbak option appears in one of these tables. For how
ezbak reads `EZBAK_` variables from the environment and from `.env` files, see
[Environment variables](environment-variables.md). For runnable container
commands, see [Running in Docker](../guides/docker.md).

The environment variable is the field name in uppercase with an `EZBAK_` prefix,
so `source_paths` becomes `EZBAK_SOURCE_PATHS`. CLI flags use their own names,
which do not always match. Some of them sit on a subcommand such as `create` or
`prune`.

A few things to know before the tables:

- ezbak reads credentials, and two other options, only from the environment, with
  no CLI flag (`aws_access_key`, `aws_secret_key`, `tz`). This keeps credentials
  out of your shell history.
- Some options apply only to the container (`EZBAK_ACTION`, `healthcheck_url`).
  They have no library field and no CLI flag, and they are in
  [Container-only options](#container-only-options) below.
- At least one storage location is required. Set `storage_paths`,
  `aws_s3_bucket_name`, or both.

## Identity and sources

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `name` | `EZBAK_NAME` | `-n`, `--name` | required |
| `source_paths` | `EZBAK_SOURCE_PATHS` | `create --source` | none |
| `sqlite_paths` | `EZBAK_SQLITE_PATHS` | `create --sqlite-path` | none |

`name` identifies the backup set and groups its files. `source_paths` lists the
files and directories to archive. To pass multiple sources, repeat `--source` on
the command line, or give a comma-separated string in the environment variable.

`sqlite_paths` names the SQLite databases to snapshot through the online-backup
API of SQLite instead of copying them as files. A database that a service holds
open is then archived consistently. Each entry is a literal path or a glob
pattern. A literal path must sit inside exactly one source path. ezbak archives
the snapshot in the place of the live file, so the archive layout does not
change. To pass multiple entries, repeat `--sqlite-path`, or give a
comma-separated string in the environment variable. See
[SQLite databases](../concepts/sqlite.md) and
[Match databases with a
pattern](../concepts/sqlite.md#match-databases-with-a-pattern).

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

The storage locations you set decide where backups go. There is no storage-type
selector. See [Storage locations](../concepts/storage-locations.md) for the
model, and [Back up to S3](../guides/s3.md) for the S3 setup.

`aws_access_key` and `aws_secret_key` are optional. Leave both unset, and ezbak
uses the credential chain of boto3. That chain covers an EC2 instance profile,
EKS IRSA, an ECS task role, the standard `AWS_*` variables, and
`~/.aws/credentials`. Setting only
one of the two is an error.

## Backup behavior

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `compression_level` | `EZBAK_COMPRESSION_LEVEL` | `create -c`, `--compression-level` | `6` |
| `strip_source_paths` | `EZBAK_STRIP_SOURCE_PATHS` | `create -s`, `--strip-source-paths` | `False` |
| `delete_source_after_backup` | `EZBAK_DELETE_SOURCE_AFTER_BACKUP` | environment only | `False` |
| `include_regex` | `EZBAK_INCLUDE_REGEX` | `create -i`, `--include-regex` | `None` |
| `exclude_regex` | `EZBAK_EXCLUDE_REGEX` | `create -e`, `--exclude-regex` | `None` |
| `use_checksums` | `EZBAK_USE_CHECKSUMS` | `create`/`restore` `--use-checksums/--no-use-checksums` | `True` |

`compression_level` is the gzip level, from 1 to 9. `strip_source_paths` flattens
a directory source, so `/source/foo.txt` archives as `foo.txt` instead of
`source/foo.txt`. `delete_source_after_backup` deletes the sources after a fully
successful backup, and never when any storage location failed. For the two
regular expressions, see
[Including and excluding files](../concepts/filtering.md).

`use_checksums` is the master switch for the `.sha256` checksum file. With the
option enabled, ezbak writes a checksum file next to each new backup archive, for
example `my-documents-20241215T143022.tgz.sha256` alongside
`my-documents-20241215T143022.tgz`. It then verifies an archive against its
checksum file on restore. The checksum file uses the same text format as
`sha256sum`, so `sha256sum -c` verifies it too. Set `use_checksums` to `false`,
and ezbak writes no new checksum files and skips verification on restore. It then
ignores any checksum file already in storage. See
[Archive integrity checksums](../concepts/checksums.md).

!!! warning "delete_source_after_backup deletes your source data"

    Treat this option with care. ezbak deletes the sources only after every
    configured storage location reports a successful write. An S3-only run with
    bad credentials fails before this step, so it never deletes the only copy of
    your data.

## Retention

Each retention field sets one keep rule. If any rule you set marks a backup, that
backup survives the prune. The rules therefore compose, instead of forcing you to
pick one policy.

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `keep_last` | `EZBAK_KEEP_LAST` | `prune --keep-last` | `None` |
| `keep_yearly` | `EZBAK_KEEP_YEARLY` | `prune -Y`, `--keep-yearly` | `None` |
| `keep_monthly` | `EZBAK_KEEP_MONTHLY` | `prune -M`, `--keep-monthly` | `None` |
| `keep_weekly` | `EZBAK_KEEP_WEEKLY` | `prune -W`, `--keep-weekly` | `None` |
| `keep_daily` | `EZBAK_KEEP_DAILY` | `prune -D`, `--keep-daily` | `None` |
| `keep_hourly` | `EZBAK_KEEP_HOURLY` | `prune -H`, `--keep-hourly` | `None` |
| `keep_minutely` | `EZBAK_KEEP_MINUTELY` | `prune -S`, `--keep-minutely` | `None` |

With no rule set, ezbak keeps every backup. A rule that you leave unset, or set
to `0`, marks nothing. See [Retention policies](../concepts/retention.md).

## Restore

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `restore_path` | `EZBAK_RESTORE_PATH` | `restore -d`, `--restore-path` | `None` |
| `restore_date` | `EZBAK_RESTORE_DATE` | `restore -t`, `--restore-date` | `None` |
| `clean_before_restore` | `EZBAK_CLEAN_BEFORE_RESTORE` | `restore --clean-before-restore` | `False` |
| `skip_if_no_backup` | `EZBAK_SKIP_IF_NO_BACKUP` | `restore --skip-if-no-backup` | `False` |
| `skip_restore_if_populated` | `EZBAK_SKIP_RESTORE_IF_POPULATED` | `restore --skip-if-populated` | `False` |
| `chown_uid` | `EZBAK_CHOWN_UID` | `restore -u`, `--uid` | `None` |
| `chown_gid` | `EZBAK_CHOWN_GID` | `restore -g`, `--gid` | `None` |

`restore_date` selects the newest backup at or before a point in time.
`clean_before_restore` empties the target as part of the restore, after a
successful extract, and it refuses to target a storage location.

`skip_if_no_backup` turns a missing backup into a clean no-op instead of a
failure. It applies only when the storage location is readable and genuinely
empty, which is the fresh-deployment case. It does not suppress a
failure to *read* a location: an unreachable bucket, or a permission error, still
fails the restore. See [An unreadable storage location is not an empty
one](../concepts/failure-behavior.md#an-unreadable-storage-location-is-not-an-empty-one).

`skip_restore_if_populated` skips the restore, as a success, when the target
already holds data other than benign noise: OS noise files, `lost+found`, and the
`.ezbak-restore-*` staging directories of ezbak. `clean_before_restore` bypasses
this guard. `chown_uid` and `chown_gid` set ownership on the restored files, and
you have to set both together. See [Restore backups](../guides/restore.md).

!!! note "skip_if_no_backup is for the CLI and container"

    A library caller does not need `skip_if_no_backup`. `restore_backup()`
    returns `RestoreOutcome.NO_BACKUP` when there is nothing to restore, so the
    caller decides how to react. The option exists so the CLI and the container
    can turn that same result into a zero exit code. See [Fresh
    deploys](../orchestration/fresh-deploys.md).

## Scheduling and timezone

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `cron` | `EZBAK_CRON` | container only | `None` |
| `tz` | `EZBAK_TZ` | environment only | `None` |
| system timezone | `TZ` | container only | `Etc/UTC` |

`cron` turns the container into a scheduled service. `tz` sets the timezone for
backup timestamps. When `tz` is unset, ezbak uses the system timezone, which the
`TZ` environment variable controls inside a container. `TZ` is a standard system
variable, not an `EZBAK_` option, so it has no library field and no CLI flag. See
[TZ and EZBAK_TZ](environment-variables.md#tz-and-ezbak_tz).

## Logging

| Field | Environment variable | CLI flag | Default |
| --- | --- | --- | --- |
| `log_level` | `EZBAK_LOG_LEVEL` | `-v`, `-vv` | `INFO` |
| `log_file` | `EZBAK_LOG_FILE` | `--log-file` | `None` |
| `log_prefix` | `EZBAK_LOG_PREFIX` | `--log-prefix` | `None` |

`log_level` accepts `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`.
On the CLI, `-v` raises the level to `DEBUG` and `-vv` raises it to `TRACE`.
`log_file` also writes the logs to a file. `log_prefix` adds a prefix to every log
line, which helps when several ezbak tasks share one log stream.

## Container-only options

These live on the container adapter, not on the library `BackupConfig`. They have
no CLI flag.

| Setting | Environment variable | Default |
| --- | --- | --- |
| Action | `EZBAK_ACTION` | none |
| Cron jitter | `EZBAK_CRON_JITTER` | `60` |
| Healthcheck URL | `EZBAK_HEALTHCHECK_URL` | `None` |
| Backup on shutdown | `EZBAK_BACKUP_ON_SHUTDOWN` | `false` |
| Pre-backup hook | `EZBAK_PRE_BACKUP_HOOK` | `None` |
| Post-backup hook | `EZBAK_POST_BACKUP_HOOK` | `None` |
| Pre-restore hook | `EZBAK_PRE_RESTORE_HOOK` | `None` |
| Post-restore hook | `EZBAK_POST_RESTORE_HOOK` | `None` |
| Hook timeout | `EZBAK_HOOK_TIMEOUT` | `300` |

`EZBAK_ACTION` is `backup` or `restore`, and it is required to run the container.

`EZBAK_CRON_JITTER` sets the seconds of random delay that ezbak adds to each
scheduled run. A fleet that shares one cron therefore does not reach a storage
location at the same instant. Set `0` to disable the delay.

`EZBAK_HEALTHCHECK_URL` pings a monitor after each run, scheduled or one-shot.
See [Monitoring](../orchestration/monitoring.md).

`EZBAK_BACKUP_ON_SHUTDOWN` takes one final backup when a cron backup container
receives `SIGTERM` or `SIGINT`. See [Final backup on
shutdown](../guides/docker.md#final-backup-on-shutdown).

The four hook variables run a shell command before or after a container backup or
restore, and `EZBAK_HOOK_TIMEOUT` bounds how long a hook can run. See
[Container lifecycle hooks](../guides/hooks.md).

*[gzip]: GNU zip compression
