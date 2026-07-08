[![Tests](https://github.com/natelandau/ezbak/actions/workflows/test.yml/badge.svg)](https://github.com/natelandau/ezbak/actions/workflows/test.yml) [![codecov](https://codecov.io/gh/natelandau/ezbak/graph/badge.svg?token=lR581iFOIE)](https://codecov.io/gh/natelandau/ezbak)

# ezbak

A simple backup tool that creates, prunes, and restores compressed archives of your files. Use it as a Python package, a command-line tool, or a Docker container.

## Features

- Create tar-gzipped (`.tgz`) backups of files and directories
- Store backups on the local filesystem, in AWS S3, or both at once
- Filter files with include and exclude regex patterns
- Prune old backups with count-based or time-based retention policies
- Restore the latest backup to any location
- Run scheduled backups in a container with a cron expression

## Table of Contents

- [Installation](#installation)
- [Usage](#usage)
  - [Python package](#python-package)
  - [Command line](#command-line)
  - [Docker container](#docker-container)
- [Core concepts](#core-concepts)
  - [Backup names](#backup-names)
  - [Storage destinations](#storage-destinations)
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

### Python package

Build a `BackupConfig` to describe what to back up and where, then pass it to `EZBak`:

```python
from pathlib import Path
from ezbak import EZBak, BackupConfig

backups = EZBak(
    BackupConfig(
        name="my-backup",
        source_paths=[Path("/path/to/source")],
        storage_paths=[Path("/path/to/destination")],
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
```

For quick scripts, `ezbak(**kwargs)` is a shortcut that builds the `BackupConfig` for you. These two calls are equivalent:

```python
from ezbak import EZBak, BackupConfig, ezbak

backups = ezbak(name="my-backup", source_paths=["/data"], storage_paths=["/backups"])
backups = EZBak(BackupConfig(name="my-backup", source_paths=["/data"], storage_paths=["/backups"]))
```

An `EZBak` instance exposes `create_backup()`, `list_backups()`, `prune_backups()`, `restore_backup()`, and `get_latest_backup()`.

`create_backup()` raises `BackupFailedError` when a configured destination can't be used, so a failed backup never looks like a success. It still writes to every destination that works, so a partial failure keeps the copies that succeeded. Catch the error to handle a failed run:

```python
from ezbak.exceptions import BackupFailedError

try:
    backups.create_backup()
except BackupFailedError as error:
    print(f"Backup failed for: {error.failed_destinations}")
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

# Restore the latest backup
ezbak --name my-documents --storage ~/Backups restore --destination ~/restore
```

To back up to S3 from the command line, pass `--s3-bucket` and provide credentials through the `EZBAK_AWS_ACCESS_KEY` and `EZBAK_AWS_SECRET_KEY` environment variables:

```bash
export EZBAK_AWS_ACCESS_KEY="your-access-key"
export EZBAK_AWS_SECRET_KEY="your-secret-key"

ezbak --name my-documents --storage ~/Backups --s3-bucket my-bucket create --source ~/Documents
```

### Docker container

The container reads its configuration from `EZBAK_`-prefixed environment variables.

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

# Restore the latest backup
docker run -it \
    -v /path/to/backups:/backups:ro \
    -v /path/to/restore:/restore \
    -e EZBAK_ACTION=restore \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_RESTORE_PATH=/restore \
    ghcr.io/natelandau/ezbak:latest
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

### Storage destinations

ezbak sends each backup to whatever destinations you configure. There is no separate storage-type setting: the destinations you provide decide where backups go.

- Set `storage_paths` to back up to one or more local directories.
- Set `aws_s3_bucket_name` (with `aws_access_key` and `aws_secret_key`) to back up to S3.
- Set both to write every backup to local storage and S3 at the same time.

At least one destination is required.

If a configured destination can't be used, whether from bad S3 credentials, an unreachable bucket, or a local directory ezbak can't create, the run fails instead of reporting success. The library raises `BackupFailedError`, and the `ezbak create` command and the one-shot container exit with a non-zero status. A scheduled container (`EZBAK_CRON`) logs the error and keeps running, so the next scheduled run retries, and it pings the failure endpoint when `EZBAK_HEALTHCHECK_URL` is set. Any backups that reached a working destination are kept.

Restores fail the same way. If ezbak can't download, read, or extract the archive, the library raises `RestoreFailedError`, and the `ezbak restore` command and the one-shot container exit non-zero. A scheduled restore logs the error and keeps the container running.

### Retention policies

ezbak keeps backups with one of two policies. You cannot combine them: if you set `max_backups`, the time-based options are ignored.

Count-based retention keeps a fixed number of the most recent backups:

```python
EZBak(
    BackupConfig(
        name="my-backup",
        source_paths=[Path("/path/to/source")],
        storage_paths=[Path("/path/to/destination")],
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
        storage_paths=[Path("/path/to/destination")],
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

`BackupConfig` accepts the following options. The same names work as `EZBAK_`-prefixed environment variables for the container.

Storage and sources:

```python
name="my-backup"                     # Identifier for this backup set (required)
source_paths=[Path("/path/to/src")]  # Files or directories to back up
storage_paths=[Path("/backups")]     # Local destination directories
aws_s3_bucket_name="my-bucket"       # S3 bucket to back up to
aws_s3_bucket_path="prefix/path"     # Optional prefix within the bucket
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
delete_src_after_backup=False # Delete sources after a successful backup
include_regex=r"\.txt$"       # Only back up files matching this pattern
exclude_regex=r"temp|cache"   # Skip files matching this pattern
```

Restore:

```python
restore_path=Path("/restore")  # Where to restore (or pass it to restore_backup())
clean_before_restore=True      # Empty the restore path first
chown_uid=1000                 # Set owner on restored files
chown_gid=1000                 # Set group on restored files
```

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
TZ="America/New_York"         # Timezone for backup timestamps
EZBAK_HEALTHCHECK_URL="https://hc-ping.com/your-uuid"  # Monitor scheduled runs
```

When you set `EZBAK_HEALTHCHECK_URL`, a scheduled container pings that URL after every run: the base URL on success, and the URL with `/fail` appended on failure. Point it at a monitor like [Healthchecks.io](https://healthchecks.io) to get alerted when a scheduled backup fails or stops running altogether. The ping never blocks or fails the backup itself. This applies only to scheduled runs (`EZBAK_CRON`); a one-shot container run reports its result through the exit code instead.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for more information.
