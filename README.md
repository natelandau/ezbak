[![Tests](https://github.com/natelandau/ezbak/actions/workflows/test.yml/badge.svg)](https://github.com/natelandau/ezbak/actions/workflows/test.yml) [![codecov](https://codecov.io/gh/natelandau/ezbak/graph/badge.svg?token=lR581iFOIE)](https://codecov.io/gh/natelandau/ezbak)

# ezbak

A simple backup management tool that can be used both as a command-line interface and as a Python package. ezbak provides automated backup creation, management, and cleanup operations with support for multiple destinations, compression, and intelligent retention policies.

## Features

-   Create compressed backups of files and directories in tgz (tar & gzip) format
-   Support for multiple backup storage locations
-   Intelligent retention policies (time-based and count-based)
-   File filtering with regex patterns
-   Time-based backup labeling (yearly, monthly, weekly, daily, hourly, minutely)
-   Restore functionality
-   Run as a python package, cli script, or docker container

## Installation

ezbak can be used as a python package, cli script, or docker container.

### Python Package

```bash
# with uv
uv add ezbak

# with pip
pip install ezbak
```

### CLI Script

```bash
# With uv
uv tool install ezbak

# With pip
python -m pip install --user ezbak
```

## Usage

### Python Package

ezbak is primarily designed to be used as a Python package in your projects:

```python
from pathlib import Path
from ezbak import ezbak

# Initialize backup manager
backup_manager = ezbak(
    name="my-backup",
    source_paths=[Path("/path/to/source")],
    storage_paths=[Path("/path/to/destination")],
    retention_yearly=1,
    retention_monthly=12,
    retention_weekly=4,
    retention_daily=7,
    retention_hourly=24,
    retention_minutely=60,
)

# Create a backup
backup_files = backup_manager.create_backup()

# List existing backups
backups = backup_manager.list_backups()

# Prune old backups
deleted_files = backup_manager.prune_backups()

# Restore latest backup and clean the restore directory before restoring
backup_manager.restore_backup(destination=Path("/path/to/restore"), clean_before_restore=True)
```

### CLI Script

```bash
# help
ezbak [subcommand] --help

# Create a backup
ezbak create --name my-backup --source /path/to/source --storage /path/to/destination

# List backups
ezbak list --name my-backup --storage /path/to/backups

# Prune backups
ezbak prune --name my-backup --storage /path/to/backups --max-backups 10

# Restore a backup
ezbak restore --name my-backup --storage /path/to/backups --destination /path/to/restore
```

### Docker Container

```bash
docker run -it ghcr.io/natelandau/ezbak:latest \
    -e EZBAK_ACTION=backup \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_SOURCE_PATHS=/path/to/source \
    -e EZBAK_STORAGE_PATHS=/path/to/destination
    # ...
```

## Configuration

ezbak takes a number of configuration options.

### Backup Names

The name is used to identify the backup in the logs and in the backup filenames. A timestamp and label are automatically inferred and do not need to be provided.

The name is used by ezbak to identify previously created backups, allowing multiple backups to be stored in the same storage location.

-   Backup files are named in the format: `{name}-{timestamp}-{period_label}.tgz`
-   When `label_time_units` is False, the period_label is omitted.
-   If a backup with the same name exists, a UUID is appended to prevent conflicts.
-   The timestamp format is ISO 8601: `YYYYMMDDTHHMMSS`

If desired, you can rename the backup files using the `rename_files` option. This will ensure the naming is consistent across backups.

### Retention Policies

By default, all backups are kept. To prune backups you can use either the `max_backups` option or specify time-based retention policies.

Note that `max_backups` and the time-based retention policies are mutually exclusive, and if both are provided, only `max_backups` will be used.

#### Max Backups

Retains the most recent specified number of backups

#### Time-Based Retention Policies

Time based retention polices allow keeping a specific number of backups for each time unit. The time units are:

-   `yearly`
-   `monthly`
-   `weekly`
-   `daily`
-   `hourly`
-   `minutely`

If any time-based policy is set, all non-set policies default to keep one backup.

### Including and Excluding Files

Any files or directories specified as source paths are included in the backup. With the exception of this global exclude list:

-   `.DS_Store`
-   `@eaDir`
-   `.Trashes`
-   `__pycache__`
-   `Thumbs.db`
-   `IconCache.db`

#### Include by Regex

When set, only files matching the regex pattern will be included in the backup.

#### Exclude by Regex

When set, files matching the regex pattern will be excluded from the backup.

## Configuration Options

The following options can be set via the CLI, Python API, or environment variables.

| Description | CLI | Python | Environment Variable |
| --- | --- | --- | --- |
| Backup name | `--name` | `name` | `EZBAK_NAME` |
| List of paths containing the content to backup | `--source` | `source_paths` | `EZBAK_SOURCE_PATHS` |
| List of paths where backups will be stored | `--storage` | `storage_paths` | `EZBAK_STORAGE_PATHS` |
| Regex pattern to exclude files. Defaults to `None`. | `--exclude-regex` | `exclude_regex` | `EZBAK_EXCLUDE_REGEX` |
| Regex pattern to include files. Defaults to `None`. | `--include-regex` | `include_regex` | `EZBAK_INCLUDE_REGEX` |
| Whether to label time units in filenames. Defaults to `True`. | `--no-label` | `label_time_units` | `EZBAK_LABEL_TIME_UNITS` |
| Whether to rename files. Defaults to `False`. | `--rename-files` | `rename_files` | `EZBAK_RENAME_FILES` |
| Compression level (1-9). Defaults to `9`. | `--compression-level` | `compression_level` | `EZBAK_COMPRESSION_LEVEL` |
| Maximum number of backups to keep. Defaults to `None`. | `--max-backups` | `max_backups` | `EZBAK_MAX_BACKUPS` |
| Number of yearly backups to keep. Defaults to `None`. | `--yearly` | `retention_yearly` | `EZBAK_RETENTION_YEARLY` |
| Number of monthly backups to keep. Defaults to `None`. | `--monthly` | `retention_monthly` | `EZBAK_RETENTION_MONTHLY` |
| Number of weekly backups to keep. Defaults to `None`. | `--weekly` | `retention_weekly` | `EZBAK_RETENTION_WEEKLY` |
| Number of daily backups to keep. Defaults to `None`. | `--daily` | `retention_daily` | `EZBAK_RETENTION_DAILY` |
| Number of hourly backups to keep. Defaults to `None`. | `--hourly` | `retention_hourly` | `EZBAK_RETENTION_HOURLY` |
| Number of minutely backups to keep. Defaults to `None`. | `--minutely` | `retention_minutely` | `EZBAK_RETENTION_MINUTELY` |
| Logging level. Defaults to `INFO`. | `--log-level` | `log_level` | `EZBAK_LOG_LEVEL` |
| Path to log file. Defaults to `None`. | `--log-file` | `log_file` | `EZBAK_LOG_FILE` |
| Optional prefix for log messages. Defaults to `None`. | `--log-prefix` | `log_prefix` | `EZBAK_LOG_PREFIX` |
| Path to restore the backup to. Defaults to `None`. | `--restore-path` | `restore_path` | `EZBAK_RESTORE_PATH` |
| Whether to clean the restore path before restoring. Defaults to `False`. | `--clean-before-restore` | `clean_before_restore` | `EZBAK_CLEAN_BEFORE_RESTORE` |
| User ID to change the ownership of restored files to. Defaults to `None`. | `--uid` | `chown_user` | `EZBAK_CHOWN_USER` |
| Group ID to change the ownership of restored files to. Defaults to `None`. | `--gid` | `chown_group` | `EZBAK_CHOWN_GROUP` |
| Action to perform. One of `backup` or `restore`. Defaults to `None`. | N/A | N/A | `EZBAK_ACTION` |
| Cron expression to schedule the backup. Example: `*/1 * * * *` | N/A | N/A | `EZBAK_CRON` |
| Timezone for backup timestamps. Defaults to system timezone. | `tz` | N/A | `EZBAK_TZ` |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for more information.
