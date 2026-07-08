[![Tests](https://github.com/natelandau/ezbak/actions/workflows/test.yml/badge.svg)](https://github.com/natelandau/ezbak/actions/workflows/test.yml) [![codecov](https://codecov.io/gh/natelandau/ezbak/graph/badge.svg?token=lR581iFOIE)](https://codecov.io/gh/natelandau/ezbak)

# ezbak

ezbak moves shared state between jobs and hosts in an orchestrated environment like Nomad or Kubernetes. It creates, prunes, and restores compressed archives on the local filesystem, in AWS S3, or both. The Docker container is the main way to run it. A Python package and a command-line tool are included for scripting and one-off use.

ezbak is a small, focused backup manager. It does not aim to replace restic, borg, or a full backup system.

## Features

- Create tar-gzipped (`.tgz`) backups of files and directories
- Store backups on the local filesystem, in AWS S3, or both at once
- Filter files with include and exclude regex patterns
- Prune old backups with count-based or time-based retention policies
- Restore the latest backup, or the newest backup at or before a point in time
- Run scheduled backups in a container with a cron expression
- Ping a healthcheck monitor so a silent scheduled failure gets noticed

## The orchestration pattern

ezbak is built for a job that owns some state, a database volume, a cache, or uploaded files, running under an orchestrator that can place it on any host. The backup follows the job so a restart or a move to a new host comes up with the state already in place.

The canonical setup runs the same container image as three cooperating tasks around the job:

- A **sidecar** takes backups on a cron schedule (`EZBAK_ACTION=backup` with `EZBAK_CRON`) while the job runs.
- A **post-stop** task takes one final backup (`EZBAK_ACTION=backup`, no cron) before the orchestrator tears the job down.
- A **pre-start** task fetches the most recent backup and stages it on the target host (`EZBAK_ACTION=restore`) before the job starts.

Point every task at the same S3 bucket, or shared storage, and the backups follow the job wherever it lands. Set `EZBAK_NAME` to the same value across all three so they operate on one backup set.

> **Note:** On a fresh deployment there is no backup yet. Set `EZBAK_RESTORE_IF_EXISTS=true` (CLI: `restore --if-exists`) so the pre-start restore treats a missing backup as a clean no-op and exits zero, letting the job start. A real download or extract failure still fails the run.

The Python package and CLI use the same configuration and are covered below for local scripting and testing.

## Table of Contents

- [Installation](#installation)
- [Usage](#usage)
  - [Docker container](#docker-container)
  - [Python package](#python-package)
  - [Command line](#command-line)
- [Core concepts](#core-concepts)
  - [Backup names](#backup-names)
  - [Storage locations](#storage-locations)
  - [Retention policies](#retention-policies)
  - [Including and excluding files](#including-and-excluding-files)
- [Configuration reference](#configuration-reference)
- [Environment variables](#environment-variables)
- [Contributing](#contributing)

## Installation

ezbak requires Python 3.11 or higher.

Install the package for use in your own code:

```bash
uv add ezbak      # with uv
pip install ezbak # with pip
```

Install the command-line tool on its own:

```bash
uv tool install ezbak                  # with uv
python -m pip install --user ezbak     # with pip
```

## Usage

The [Docker container](#docker-container) is the primary interface and the one to reach for in an orchestrated deployment. The [command line](#command-line) and [Python package](#python-package) share the same configuration and are handy for scripting, local testing, and one-off backups.

### Docker container

The container reads its configuration from `EZBAK_`-prefixed environment variables. The examples below are the building blocks of [the orchestration pattern](#the-orchestration-pattern): a scheduled run for the sidecar, a one-shot backup for the post-stop task, and a one-shot restore for the pre-start task.

```bash
# Create a backup and keep the 7 most recent
docker run -it \
    -v /path/to/source:/source:ro \
    -v /path/to/backups:/backups \
    -e EZBAK_ACTION=backup \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_SOURCE_PATHS=/source \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_MAX_BACKUPS=7 \
    ghcr.io/natelandau/ezbak:latest

# Run backups on a schedule (daily at 2 AM)
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

# Restore the latest backup (pre-start task; skip cleanly if no backup exists yet)
docker run -it \
    -v /path/to/backups:/backups:ro \
    -v /path/to/restore:/restore \
    -e EZBAK_ACTION=restore \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_RESTORE_PATH=/restore \
    -e EZBAK_RESTORE_IF_EXISTS=true \
    ghcr.io/natelandau/ezbak:latest

# Restore the newest backup at or before a point in time
docker run -it \
    -v /path/to/backups:/backups:ro \
    -v /path/to/restore:/restore \
    -e EZBAK_ACTION=restore \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_RESTORE_PATH=/restore \
    -e EZBAK_RESTORE_DATE=202412 \
    ghcr.io/natelandau/ezbak:latest
```

### Python package

Build a `BackupConfig` to describe what to back up and where, then pass it to `EZBak`:

```python
from pathlib import Path
from ezbak import EZBak, BackupConfig

backups = EZBak(
    BackupConfig(
        name="my-backup",
        source_paths=[Path("/path/to/source")],
        storage_paths=[Path("/path/to/backups")],
        # Keep 1 yearly, 12 monthly, 4 weekly, 7 daily, 24 hourly, 60 minutely
        retention_yearly=1,
        retention_monthly=12,
        retention_weekly=4,
        retention_daily=7,
        retention_hourly=24,
        retention_minutely=60,
    )
)

backups.create_backup()
print([backup.name for backup in backups.list_backups()])
backups.prune_backups()

# Restore the latest backup
backups.restore_backup(restore_path=Path("/path/to/restore_location"))

# Restore an older backup instead of the latest
backup = backups.get_backup_as_of("20241201")
if backup:
    backups.restore_backup(restore_path=Path("/path/to/restore_location"), backup=backup)
```

For quick scripts, `ezbak(**kwargs)` is a shortcut that builds the `BackupConfig` for you. These two calls are equivalent:

```python
from ezbak import EZBak, BackupConfig, ezbak

backups = ezbak(name="my-backup", source_paths=["/data"], storage_paths=["/backups"])
backups = EZBak(BackupConfig(name="my-backup", source_paths=["/data"], storage_paths=["/backups"]))
```

An `EZBak` instance exposes `create_backup()`, `list_backups()`, `prune_backups()`, `restore_backup()`, `get_latest_backup()`, and `get_backup_as_of()`. Call `prune_backups(dry_run=True)` to get back the list of backups the retention policy would delete without removing any of them.

`get_backup_as_of(point_in_time)` returns the newest backup at or before the end of the period you name, so you can restore an older backup instead of the latest. Pass its result to `restore_backup(backup=...)`. An explicit `backup` argument takes priority over a configured `restore_date`, which in turn takes priority over the latest backup.

`create_backup()` raises `BackupFailedError` when a configured storage location can't be used, so a failed backup never looks like a success. It still writes to every storage location that works, so a partial failure keeps the copies that succeeded. The error's `failed_storage_locations` names the destinations that failed, and `created_backups` holds the `Backup` objects that were written before the failure. Catch the error to handle a failed run:

```python
from ezbak.exceptions import BackupFailedError

try:
    backups.create_backup()
except BackupFailedError as error:
    print(f"Backup failed for: {error.failed_storage_locations}")
    print(f"Backups that succeeded: {[backup.name for backup in error.created_backups]}")
```

`restore_backup()` raises `RestoreFailedError` when the archive can't be downloaded, read, or extracted, so a failed restore never looks like a success. This matters most with `clean_before_restore`, which empties the target before extracting: a silent failure would leave you with an empty directory and no error. The method returns `False` only when there is no backup to restore. Catch the error to handle a failed run:

```python
from ezbak.exceptions import RestoreFailedError

try:
    backups.restore_backup()
except RestoreFailedError as error:
    print(f"Restore failed: {error}")
```

Every ezbak error subclasses `EZBakError`, so you can catch that one type to handle any failure.

### Command line

The `name` and `storage` options are global and come before the subcommand. Run `ezbak --help` or `ezbak create --help` to see every option.

```bash
# Create a backup
ezbak --name my-documents --storage ~/Backups create --source ~/Documents

# List backups
ezbak --name my-documents --storage ~/Backups list

# Prune old backups, keeping the 10 most recent
ezbak --name my-documents --storage ~/Backups prune --max-backups 10

# Preview a prune without deleting anything
ezbak --name my-documents --storage ~/Backups prune --max-backups 10 --dry-run

# Restore the latest backup
ezbak --name my-documents --storage ~/Backups restore --restore-path ~/restore

# Restore the newest backup at or before a point in time
ezbak --name my-documents --storage ~/Backups restore --restore-path ~/restore --restore-date 202412

# Restore the latest backup, but exit cleanly if none exists yet
ezbak --name my-documents --storage ~/Backups restore --restore-path ~/restore --if-exists
```

Pass `restore --if-exists` when a missing backup should not be an error. Without it, a restore that finds no backup exits non-zero; with it, the command logs that there is nothing to restore and exits zero. A real download or extract failure still exits non-zero either way.

`restore --restore-date` (short `-t`) restores the newest backup at or before the end of the period you name, not the backup closest to it: `--restore-date 202412` restores the last backup from December 2024, even if that backup landed on December 30. Accepted formats, from a year down to a second, are `YYYY`, `YYYYMM`, `YYYYMMDD`, `YYYYMMDDTHH`, `YYYYMMDDTHHMM`, and `YYYYMMDDTHHMMSS`. The full `YYYYMMDDTHHMMSS` form matches the timestamp the `list` command prints for each backup, so you can copy a value straight from `list` output to restore that exact backup.

To back up to S3 from the command line, pass `--s3-bucket` and provide credentials through the `EZBAK_AWS_ACCESS_KEY` and `EZBAK_AWS_SECRET_KEY` environment variables:

```bash
export EZBAK_AWS_ACCESS_KEY="your-access-key"
export EZBAK_AWS_SECRET_KEY="your-secret-key"

ezbak --name my-documents --storage ~/Backups --s3-bucket my-bucket create --source ~/Documents
```

## Core concepts

### Backup names

Every backup needs a name that identifies it in logs and groups its files. ezbak adds a timestamp automatically.

The filename format is `{name}-{timestamp}.tgz`, for example:

- `my-documents-20241215T143022.tgz`
- `database-backup-20241215T020000.tgz`

A few details worth knowing:

- Multiple backup sets can share one storage location, because each set matches only its own name.
- Timestamps use the format `YYYYMMDDTHHMMSS`.
- A duplicate name gets a short unique suffix so files never overwrite each other.

### Storage locations

ezbak sends each backup to whatever storage locations you configure. There is no separate storage-type setting: the locations you provide decide where backups go.

- Set `storage_paths` to back up to one or more local directories.
- Set `aws_s3_bucket_name` (with `aws_access_key` and `aws_secret_key`) to back up to S3.
- Set both to write every backup to local storage and S3 at the same time.

At least one storage location is required.

If a configured storage location can't be used, whether from bad S3 credentials, an unreachable bucket, or a local directory ezbak can't create, the run fails instead of reporting success. The library raises `BackupFailedError`, and the `ezbak create` command and the one-shot container exit with a non-zero status. A scheduled container (`EZBAK_CRON`) logs the error and keeps running, so the next scheduled run retries, and it pings the failure endpoint when `EZBAK_HEALTHCHECK_URL` is set. Any backups that reached a working storage location are kept.

Restores fail the same way. If ezbak can't download, read, or extract the archive, the library raises `RestoreFailedError`, and the `ezbak restore` command and the one-shot container exit non-zero. A scheduled restore logs the error and keeps the container running.

### Retention policies

ezbak keeps backups with one of two policies. You cannot combine them: if you set `max_backups`, the time-based options are ignored.

Count-based retention keeps a fixed number of the most recent backups:

```python
EZBak(
    BackupConfig(
        name="my-backup",
        source_paths=[Path("/path/to/source")],
        storage_paths=[Path("/path/to/backups")],
        max_backups=10,
    )
)
```

Time-based retention keeps different amounts for different periods. Any period you leave unset keeps 1 backup:

```python
EZBak(
    BackupConfig(
        name="my-backup",
        source_paths=[Path("/path/to/source")],
        storage_paths=[Path("/path/to/backups")],
        retention_daily=7,
        retention_weekly=4,
        retention_monthly=12,
        retention_yearly=3,
    )
)
```

### Including and excluding files

By default ezbak backs up every file in your source paths, apart from these always-excluded names: `.DS_Store`, `@eaDir`, `.Trashes`, `__pycache__`, `Thumbs.db`, and `IconCache.db`.

Narrow the selection with regex patterns:

- `include_regex` backs up only files whose path matches the pattern.
- `exclude_regex` skips files whose path matches the pattern.

```python
EZBak(
    BackupConfig(
        name="logs",
        source_paths=[Path("/var/log")],
        storage_paths=[Path("/backups")],
        include_regex=r"\.log$",   # only .log files
        exclude_regex=r"debug",    # skip anything matching "debug"
        max_backups=10,
    )
)
```

## Configuration reference

`BackupConfig` accepts the options below. Each library field name also works as an `EZBAK_`-prefixed environment variable: uppercase the field and add the prefix, so `source_paths` becomes `EZBAK_SOURCE_PATHS`. The CLI uses its own flag names, which do not always match the field names. This table maps the three surfaces for the options whose names differ or that you set most often. Every other option follows the same field-to-`EZBAK_` rule; run `ezbak create --help` or `ezbak prune --help` for the create and prune flags (for example `retention_yearly` is `prune --yearly`).

| Library field (`BackupConfig`) | Environment variable | CLI flag |
| --- | --- | --- |
| `name` | `EZBAK_NAME` | `--name`, `-n` |
| `source_paths` | `EZBAK_SOURCE_PATHS` | `create --source` |
| `storage_paths` | `EZBAK_STORAGE_PATHS` | `--storage` |
| `aws_s3_bucket_name` | `EZBAK_AWS_S3_BUCKET_NAME` | `--s3-bucket` |
| `aws_s3_bucket_prefix` | `EZBAK_AWS_S3_BUCKET_PREFIX` | `--s3-bucket-prefix` |
| `max_backups` | `EZBAK_MAX_BACKUPS` | `prune --max-backups`, `-x` |
| `restore_path` | `EZBAK_RESTORE_PATH` | `restore --restore-path`, `-d` |
| `restore_date` | `EZBAK_RESTORE_DATE` | `restore --restore-date`, `-t` |
| `clean_before_restore` | `EZBAK_CLEAN_BEFORE_RESTORE` | `restore --clean-before-restore` |
| `restore_if_exists` | `EZBAK_RESTORE_IF_EXISTS` | `restore --if-exists` |
| `chown_uid` | `EZBAK_CHOWN_UID` | `restore --uid`, `-u` |
| `chown_gid` | `EZBAK_CHOWN_GID` | `restore --gid`, `-g` |
| `log_level` | `EZBAK_LOG_LEVEL` | `-v`, `-vv` |

Storage and sources:

```python
name="my-backup"                     # Identifier for this backup set (required)
source_paths=[Path("/path/to/src")]  # Files or directories to back up
storage_paths=[Path("/backups")]     # Local storage directories
aws_s3_bucket_name="my-bucket"       # S3 bucket to back up to
aws_s3_bucket_prefix="prefix/path"   # Optional key prefix within the bucket
aws_access_key="your-access-key"     # S3 credentials
aws_secret_key="your-secret-key"
```

Retention (choose count-based or time-based):

```python
max_backups=10          # Count-based: keep the 10 most recent

retention_yearly=3      # Time-based: keep this many of each period
retention_monthly=12
retention_weekly=4
retention_daily=7
retention_hourly=24
retention_minutely=60
```

Backup behavior:

```python
compression_level=9           # gzip level, 1 to 9 (default: 9)
strip_source_paths=False      # Flatten directory sources in the archive
delete_source_after_backup=False # Delete sources after a successful backup
include_regex=r"\.txt$"       # Only back up files matching this pattern
exclude_regex=r"temp|cache"   # Skip files matching this pattern
```

Restore:

```python
restore_path=Path("/restore")  # Where to restore (or pass it to restore_backup())
restore_date="202412"          # Restore the newest backup at or before this point in time
clean_before_restore=True      # Empty the restore path first
restore_if_exists=True         # Treat "no backup to restore" as success, not an error
chown_uid=1000                 # Set owner on restored files
chown_gid=1000                 # Set group on restored files
```

The library reports a missing backup directly: `restore_backup()` returns `False` when there is nothing to restore and raises only on a real failure, so a Python caller decides how to react without `restore_if_exists`. The setting exists so the CLI and container can make the same distinction through their exit codes.

Logging:

```python
log_level="INFO"                     # TRACE, DEBUG, INFO, WARNING, or ERROR (default: INFO)
log_file=Path("/var/log/ezbak.log")  # Also write logs to this file
log_prefix="BACKUP"                  # Prefix added to every log line
```

## Environment variables

The container, and the credential fallback for the CLI, read every option from environment variables with the `EZBAK_` prefix. For example, `source_paths` becomes `EZBAK_SOURCE_PATHS`.

```bash
export EZBAK_NAME="my-backup"
export EZBAK_SOURCE_PATHS="/path/to/source"
export EZBAK_STORAGE_PATHS="/path/to/backups"
export EZBAK_RETENTION_DAILY=7
```

Some options apply only when running the container:

```bash
EZBAK_ACTION=backup           # backup or restore
EZBAK_CRON="0 2 * * *"        # Cron schedule (daily at 2 AM)
EZBAK_RESTORE_PATH=/restore   # Where a restore writes files
EZBAK_RESTORE_DATE=202412     # Restore action: newest backup at or before this point in time
TZ="America/New_York"         # Timezone for backup timestamps
EZBAK_HEALTHCHECK_URL="https://hc-ping.com/your-uuid"  # Monitor scheduled runs
```

`EZBAK_RESTORE_DATE` only applies to the `restore` action. See [Command line](#command-line) for the accepted date formats and the at-or-before matching rule.

When you set `EZBAK_HEALTHCHECK_URL`, a scheduled container pings that URL after every run: the base URL on success, and the URL with `/fail` appended on failure. Point it at a monitor like [Healthchecks.io](https://healthchecks.io) to get alerted when a scheduled backup fails or stops running altogether. The ping never blocks or fails the backup itself. This applies only to scheduled runs (`EZBAK_CRON`); a one-shot container run reports its result through the exit code instead.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for more information.
