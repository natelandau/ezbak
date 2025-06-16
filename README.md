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
    sources=[Path("/path/to/source")],
    destinations=[Path("/path/to/destination")],
    time_based_policy={
        "yearly": 1,
        "monthly": 12,
        "weekly": 4,
        "daily": 7,
        "hourly": 24,
        "minutely": 60,
    },
    log_level="INFO",
    log_file=Path("/path/to/log.txt"),
    exclude_regex=r"\.DS_Store$",
    label_time_units=True,
)

# Create a backup
backup_files = backup_manager.create_backup()

# List existing backups
backups = backup_manager.list_backups()

# Prune old backups
deleted_files = backup_manager.prune_backups()

# Restore latest backup
backup_manager.restore_latest_backup(destination=Path("/path/to/restore"))
```

#### Configuration Options

-   `name (str)`: Backup name
-   `sources (list[Path])`: List of source paths
-   `destinations (list[Path])`: List of destination paths
-   `compression_level (int)`: Compression level (1-9)
-   `max_backups (int)`: Maximum number of backups to keep
-   `time_based_policy (dict[str, int])`: Time-based retention policy (dictionary of time units and number of backups to keep)
-   `timezone (str)`: Timezone for backup timestamps
-   `log_level (str)`: Logging level
-   `log_file (Path | str)`: Path to log file
-   `exclude_regex (str)`: Regex pattern to exclude files
-   `include_regex (str)`: Regex pattern to include files
-   `label_time_units (bool)`: Whether to label time units in filenames

#### Retention Policies

If neither `max_backups` and `time_based_policy` are provided, all backups are kept. Keep in mind that these are mutually exclusive, and if both are provided, `max_backups` will be used.

The time-based retention policy is a dictionary of time units and number of backups to keep. The time units are:

-   `yearly`
-   `monthly`
-   `weekly`
-   `daily`
-   `hourly`
-   `minutely`

For example, the following policy will keep the most recent 2 yearly backups, 12 monthly backups, 4 weekly backups, 7 daily backups, 24 hourly backups, and 10 minutely backups:

```python
time_based_policy = {
    "yearly": 2,
    "monthly": 12,
    "weekly": 4,
    "daily": 7,
    "hourly": 24,
    "minutely": 10,
}
```

#### Backup Naming

    - Backup files are named in the format: `{name}-{timestamp}-{period}.{extension}`
    - When `label_time_units` is False, the period is omitted
    - If a backup with the same name exists, a UUID is appended to prevent conflicts
    - The timestamp format is ISO 8601: `YYYYMMDDTHHMMSS`

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

### Environment Variables

ezbak can be configured using environment variables with the `EZBAK_` prefix:

-   `EZBAK_NAME`: Backup name
-   `EZBAK_SOURCES`: Comma-separated list of source paths
-   `EZBAK_DESTINATIONS`: Comma-separated list of destination paths
-   `EZBAK_TZ`: Timezone for backup timestamps
-   `EZBAK_LOG_LEVEL`: Logging level
-   `EZBAK_LOG_FILE`: Path to log file
-   `EZBAK_COMPRESSION_LEVEL`: Compression level (1-9)
-   `EZBAK_TIME_BASED_POLICY`: JSON string of time-based retention policy
-   `EZBAK_MAX_BACKUPS`: Maximum number of backups to keep
-   `EZBAK_EXCLUDE_REGEX`: Regex pattern to exclude files
-   `EZBAK_INCLUDE_REGEX`: Regex pattern to include files
-   `EZBAK_LABEL_TIME_UNITS`: Whether to label time units in filenames

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for more information.
