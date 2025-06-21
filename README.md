[![Tests](https://github.com/natelandau/ezbak/actions/workflows/test.yml/badge.svg)](https://github.com/natelandau/ezbak/actions/workflows/test.yml) [![codecov](https://codecov.io/gh/natelandau/ezbak/graph/badge.svg?token=lR581iFOIE)](https://codecov.io/gh/natelandau/ezbak)

# ezbak

A simple backup management tool that can be used both as a command-line interface and as a Python package. ezbak provides automated backup creation, management, and cleanup operations with support for multiple destinations, compression, and intelligent retention policies.

## Features

-   Create compressed backups of files and directories in tgz format
-   Support for multiple backup destinations
-   Configurable compression levels
-   Intelligent retention policies (time-based and count-based)
-   File filtering with regex patterns
-   Time-based backup labeling (yearly, monthly, weekly, daily, hourly, minutely)
-   Automatic backup pruning based on retention policies
-   Restore functionality

## Installation

```bash
# with uv
uv add ezbak

# with pip
pip install ezbak
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

#### Configuration Options

-   `name (str)`: Backup name
-   `source_paths (list[Path])`: List of paths containing the content to backup
-   `storage_paths (list[Path])`: List of paths where backups will be stored
-   `compression_level (int, optional)`: Compression level (1-9). Defaults to `9`.
-   `max_backups (int, optional)`: Maximum number of backups to keep. Defaults to `None`.
-   `retention_yearly (int, optional)`: Number of yearly backups to keep. Defaults to `None`.
-   `retention_monthly (int, optional)`: Number of monthly backups to keep. Defaults to `None`.
-   `retention_weekly (int, optional)`: Number of weekly backups to keep. Defaults to `None`.
-   `retention_daily (int, optional)`: Number of daily backups to keep. Defaults to `None`.
-   `retention_hourly (int, optional)`: Number of hourly backups to keep. Defaults to `None`.
-   `retention_minutely (int, optional)`: Number of minutely backups to keep. Defaults to `None`.
-   `timezone (str, optional)`: Timezone for backup timestamps. Defaults to system timezone.
-   `log_level (str, optional)`: Logging level. Defaults to `INFO`.
-   `log_file (Path | str, optional)`: Path to log file. Defaults to `None`.
-   `exclude_regex (str, optional)`: Regex pattern to exclude files. Defaults to `None`.
-   `include_regex (str, optional)`: Regex pattern to include files. Defaults to `None`.
-   `label_time_units (bool, optional)`: Whether to label time units in filenames. Defaults to `True`.
-   `chown_user (int, optional)`: User ID to change the ownership of restored files to. Defaults to `None`.
-   `chown_group (int, optional)`: Group ID to change the ownership of restored files to. Defaults to `None`.

#### Backup Naming

-   Backup files are named in the format: `{name}-{timestamp}-{period}.{extension}`
-   When `label_time_units` is False, the period is omitted
-   If a backup with the same name exists, a UUID is appended to prevent conflicts
-   The timestamp format is ISO 8601: `YYYYMMDDTHHMMSS`

#### Retention Policies

If neither `max_backups` or any of the time-based retention policies are provided, all backups are kept. Keep in mind that `max_backups` and the time-based retention policies are mutually exclusive, and if both are provided, `max_backups` will be used.

For example, the following policy will keep the most recent 2 yearly backups, 12 monthly backups, 4 weekly backups, 7 daily backups, 24 hourly backups, and 10 minutely backups:

### Command Line Interface

For convenience, ezbak also provides a command-line interface with several subcommands:

#### Create a Backup

```bash
ezbak create --name my-backup --sources /path/to/source --destinations /path/to/destination
```

Additional options:

-   `--include-regex`: Include files matching the regex pattern
-   `--exclude-regex`: Exclude files matching the regex pattern
-   `--compression-level`: Set compression level (1-9)
-   `--no-label`: Disable time unit labeling in backup filenames

#### List Backups

```bash
ezbak list --locations /path/to/backups
```

#### Prune Backups

```bash
ezbak prune --destinations /path/to/backups --max-backups 10
```

Time-based retention options:

-   `--yearly`: Number of yearly backups to keep
-   `--monthly`: Number of monthly backups to keep
-   `--weekly`: Number of weekly backups to keep
-   `--daily`: Number of daily backups to keep
-   `--hourly`: Number of hourly backups to keep
-   `--minutely`: Number of minutely backups to keep

#### Restore Backup

```bash
ezbak restore --destination /path/to/restore
```

#### Excluded Files

Importantly, some file and directory names are always excluded from backups. These are:

-   `.DS_Store`
-   `@eaDir`
-   `.Trashes`
-   `__pycache__`
-   `Thumbs.db`
-   `IconCache.db`

### Environment Variables

ezbak can be configured using environment variables with the `EZBAK_` prefix:

-   `EZBAK_ACTION` (str): The action to perform. One of `backup` or `restore`
-   `EZBAK_NAME` (str): The name of the backup
-   `EZBAK_SOURCE_PATHS` (str): The paths to backup (Comma-separated list of paths)
-   `EZBAK_STORAGE_PATHS` (str): The paths to store the backups (Comma-separated list of paths)
-   `EZBAK_INCLUDE_REGEX` (str): The regex pattern to include files
-   `EZBAK_EXCLUDE_REGEX` (str): The regex pattern to exclude files
-   `EZBAK_COMPRESSION_LEVEL` (int): The compression level. One of `1` to `9`
-   `EZBAK_LABEL_TIME_UNITS` (bool): Whether to label time units in filenames
-   `EZBAK_RENAME_FILES` (bool): Whether to rename files
-   `EZBAK_MAX_BACKUPS` (int): The maximum number of backups to keep
-   `EZBAK_RETENTION_YEARLY` (int): The number of yearly backups to keep
-   `EZBAK_RETENTION_MONTHLY` (int): The number of monthly backups to keep
-   `EZBAK_RETENTION_WEEKLY` (int): The number of weekly backups to keep
-   `EZBAK_RETENTION_DAILY` (int): The number of daily backups to keep
-   `EZBAK_RETENTION_HOURLY` (int): The number of hourly backups to keep
-   `EZBAK_RETENTION_MINUTELY` (int): The number of minutely backups to keep
-   `EZBAK_CRON` (str): The cron expression to schedule the backup. Example: `*/1 * * * *`
-   `EZBAK_TZ` (str): The timezone to use for the backup
-   `EZBAK_LOG_LEVEL` (str): The logging level. One of `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`
-   `EZBAK_LOG_FILE` (str): The path to the log file
-   `EZBAK_LOG_PREFIX` (str): Optional prefix for log messages
-   `EZBAK_RESTORE_PATH` (str): The path to restore the backup to
-   `EZBAK_CLEAN_BEFORE_RESTORE` (bool): Whether to clean the restore path before restoring
-   `EZBAK_CHOWN_USER` (int): The user ID to change the ownership of restored files to
-   `EZBAK_CHOWN_GROUP` (int): The group ID to change the ownership of restored files to

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for more information.
