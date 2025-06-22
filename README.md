[![Tests](https://github.com/natelandau/ezbak/actions/workflows/test.yml/badge.svg)](https://github.com/natelandau/ezbak/actions/workflows/test.yml) [![codecov](https://codecov.io/gh/natelandau/ezbak/graph/badge.svg?token=lR581iFOIE)](https://codecov.io/gh/natelandau/ezbak)

# ezbak

A simple, powerful backup management tool for Python developers and system administrators. ezbak automates backup creation, management, and cleanup with support for multiple destinations, compression, and intelligent retention policies.

Use ezbak as a Python package in your code, run it from the command line, or deploy it as a Docker container.

**Core Functionality**

-   Create compressed backups (tgz format) of files and directories
-   Restore backups to any location
-   Support for multiple backup storage destinations

**Smart Management**

-   Intelligent retention policies (time-based and count-based)
-   Automatic cleanup of old backups
-   Time-based backup labeling (yearly, monthly, weekly, daily, hourly, minutely)

**Flexible Usage**

-   Python package for integration into your projects
-   Command-line interface for scripts and automation
-   Docker container for containerized environments
-   File filtering with regex patterns

## Table of Contents

-   [Installation](#installation)
-   [Quick Start](#quick-start)
-   [Usage](#usage)
-   [Manual](#manual)
-   [Common Use Cases](#common-use-cases)
-   [Configuration Options](#configuration-options)
-   [Contributing](#contributing)

## Installation

ezbak can be used as a python package, cli script, or docker container.

**Note:** ezbak requires Python 3.10 or higher.

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

## Quick Start

### Create your first backup

```python
from pathlib import Path
from ezbak import ezbak

# Simple backup with 7-day retention
backup_manager = ezbak(
    name="my-documents",
    source_paths=[Path("~/Documents")],
    storage_paths=[Path("~/Backups")],
    retention_daily=7,
)

# Create the backup
backup_files = backup_manager.create_backup()
print(f"Created backup: {backup_files}")
```

### or, using the CLI

```bash
# Create a backup
ezbak create --name my-documents --source ~/Documents --storage ~/Backups --daily 7

# List your backups
ezbak list --name my-documents --storage ~/Backups
```

## Usage

### Python Package

ezbak is primarily designed to be used as a Python package in your projects:

```python
from pathlib import Path
from ezbak import ezbak

# Initialize backup manager with retention policy
backup_manager = ezbak(
    name="my-backup",
    source_paths=[Path("/path/to/source")],
    storage_paths=[Path("/path/to/destination")],
    # Keep: 1 yearly, 12 monthly, 4 weekly, 7 daily, 24 hourly, 60 minutely
    retention_yearly=1,
    retention_monthly=12,
    retention_weekly=4,
    retention_daily=7,
    retention_hourly=24,
    retention_minutely=60,
)

# Create a backup
backup_files = backup_manager.create_backup()
print(f"Backup created: {backup_files}")

# List existing backups
backups = backup_manager.list_backups()
print(f"Found {len(backups)} backups")

# Clean up old backups based on retention policy
deleted_files = backup_manager.prune_backups()
print(f"Deleted {len(deleted_files)} old backups")

# Restore latest backup (with optional cleanup)
backup_manager.restore_backup(
    destination=Path("/path/to/restore"),
    clean_before_restore=True
)
```

### CLI Script

```bash
# Get help for any command
ezbak --help
ezbak create --help

# Create a backup with 7-day retention
ezbak create --name my-documents \
    --source ~/Documents \
    --storage ~/Backups \
    --daily 7

# List all backups for a specific backup name
ezbak list --name my-documents --storage ~/Backups

# Clean up old backups (keep only 10 most recent)
ezbak prune --name my-documents \
    --storage ~/Backups \
    --max-backups 10

# Restore the latest backup
ezbak restore --name my-documents \
    --storage ~/Backups \
    --destination ~/Restored \
    --clean-before-restore  # Optional: clear destination before restore

```

### Docker Container

```bash
# Create a backup using Docker
docker run -it \
    -v /path/to/source:/source:ro \
    -v /path/to/backups:/backups \
    -e EZBAK_ACTION=backup \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_SOURCE_PATHS=/source \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_RETENTION_DAILY=7 \
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
    -e EZBAK_RETENTION_DAILY=7 \
    -e EZBAK_CRON="0 2 * * *" \
    -e TZ=America/New_York \
    ghcr.io/natelandau/ezbak:latest

# Restore a backup
docker run -it \
    -v /path/to/backups:/backups:ro \
    -v /path/to/restore:/restore \
    -e EZBAK_ACTION=restore \
    -e EZBAK_NAME=my-backup \
    -e EZBAK_STORAGE_PATHS=/backups \
    -e EZBAK_DESTINATION=/restore \
    ghcr.io/natelandau/ezbak:latest
```

## Manual

Key concepts and configuration options for ezbak.

### Backup Names

Each backup needs a unique name to identify it in logs and organize backup files. ezbak automatically adds timestamps and labels.

**Filename Format:** `{name}-{timestamp}-{period_label}.tgz`

**Examples:**

-   `my-documents-20241215T143022-daily.tgz`
-   `database-backup-20241215T020000-weekly.tgz`

**Key Points:**

-   Multiple backup sets can share the same storage location
-   Timestamps use ISO 8601 format: `YYYYMMDDTHHMMSS`
-   Period labels (daily, weekly, etc.) can be disabled with `label_time_units=False`
-   Duplicate names get a UUID suffix to prevent conflicts

If desired, you can rename the backup files using the `rename_files` option. This will ensure the naming is consistent across backups.

### Retention Policies

Control how many backups to keep with two approaches. **Note**: You can't use both methods together. If you set max_backups, time-based retention is ignored.

#### Simple Count-Based Retention

```python
# Keep only the 10 most recent backups
backup_manager = ezbak(
    name="my-backup",
    source_paths=[Path("/path/to/source")],
    storage_paths=[Path("/path/to/destination")],
    max_backups=10
)
```

#### Time-Based Retention (Recommended)

```python
# Keep different numbers of backups for different time periods
# Unspecified time periods (hourly, minutely) default to keeping 1 backup each
backup_manager = ezbak(
    name="my-backup",
    source_paths=[Path("/path/to/source")],
    storage_paths=[Path("/path/to/destination")],
    retention_daily=7,    # Keep 7 daily backups
    retention_weekly=4,   # Keep 4 weekly backups
    retention_monthly=12, # Keep 12 monthly backups
    retention_yearly=3    # Keep 3 yearly backups
)
```

### Including and Excluding Files

By default, all files in your source paths are backed up, except for these automatically excluded files:

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

## Common Use Cases

### Daily Document Backup

```python
from pathlib import Path
from ezbak import ezbak

backup_manager = ezbak(
    name="documents",
    source_paths=[Path("~/Documents"), Path("~/Pictures")],
    storage_paths=[Path("~/Backups")],
    retention_daily=30,  # Keep 30 days of daily backups
    retention_monthly=12  # Keep 12 monthly backups
)
backup_manager.create_backup()
```

### Selective File Backup

```python
backup_manager = ezbak(
    name="logs",
    source_paths=[Path("/var/log")],
    storage_paths=[Path("/backups")],
    include_regex=r"\.log$",  # Only .log files
    exclude_regex=r"debug",   # Exclude debug logs
    max_backups=10
)
```

### Database Backup with Pre/Post Scripts

```python
import subprocess
from pathlib import Path
from ezbak import ezbak

# Dump database before backup
subprocess.run(["pg_dump", "-f", "/tmp/db_backup.sql", "mydb"])

backup_manager = ezbak(
    name="database",
    source_paths=[Path("/tmp/db_backup.sql")],
    storage_paths=[Path("/backups/database")],
    retention_hourly=24,  # Keep 24 hourly backups
    retention_daily=7,
    retention_weekly=4
)

backup_manager.create_backup()

# Cleanup temp file
Path("/tmp/db_backup.sql").unlink()
```

## Configuration Options

Configure ezbak using any combination of Python parameters, CLI arguments, or environment variables:

### Core Settings

```python
backup_manager = ezbak(
    name="my-backup",                    # Backup identifier
    source_paths=[Path("/path/to/src")], # What to backup
    storage_paths=[Path("/backups")],    # Where to store backups
)
```

### Retention Settings

```python
# Option 1: Keep a maximum number of backups
max_backups=10

# Option 2: Time-based retention (recommended)
retention_daily=7,      # Keep 7 daily backups
retention_weekly=4,     # Keep 4 weekly backups
retention_monthly=12,   # Keep 12 monthly backups
retention_yearly=3,     # Keep 3 yearly backups
retention_hourly=24,    # Keep 24 hourly backups
retention_minutely=60,  # Keep 60 minutely backups
```

### File Filtering

```python
include_regex=r"\.txt$",     # Only include .txt files
exclude_regex=r"temp|cache", # Exclude temp and cache files
```

### Backup Options

```python
compression_level=9,         # Compression level (1-9, default: 9)
label_time_units=True,       # Include time labels in filenames (default: True)
rename_files=False,          # Rename existing files (default: False)
strip_source_paths=False,    # Strip source paths from directory sources to flatten the tarfile (e.g. /source/foo.txt -> foo.txt)
```

### Restore Options

```python
restore_path=Path("/restore"),           # Where to restore files
clean_before_restore=True,               # Clear destination first
chown_user=1000,                         # Set file owner (UID)
chown_group=1000,                        # Set file group (GID)
```

### Logging

```python
log_level="INFO",                       # DEBUG, INFO, WARNING, ERROR
log_file=Path("/var/log/ezbak.log"),    # Log file path
log_prefix="BACKUP",                    # Log message prefix
```

### Environment Variables

All options can be set via environment variables using the `EZBAK_` prefix:

```bash
export EZBAK_NAME="my-backup"
export EZBAK_SOURCE_PATHS="/path/to/source"
export EZBAK_STORAGE_PATHS="/path/to/backups"
export EZBAK_RETENTION_DAILY=7
# etc.
```

### Docker-Only Options

```bash
EZBAK_ACTION=backup           # Action: backup or restore
EZBAK_CRON="0 2 * * *"        # Cron schedule (daily at 2 AM)
EZBAK_TZ="America/New_York"   # Timezone for timestamps
```

### CLI Equivalents

Most Python options have CLI equivalents. Use --help for details:

```bash
ezbak create --help     # See all create options
ezbak restore --help    # See all restore options
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for more information.
