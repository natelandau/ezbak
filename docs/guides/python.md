# Using the Python library

The library lets you drive ezbak from your own code: a management script, a
scheduled job, or a larger application. Build a `BackupConfig`, pass it to
`EZBak`, and call the backup methods.

## A first backup

```python
from pathlib import Path
from ezbak import EZBak, BackupConfig

backups = EZBak(
    BackupConfig(
        name="my-backup",
        source_paths=[Path("/data")],
        storage_paths=[Path("/backups")],
        max_backups=10,
    )
)

backups.create_backup()
print([backup.name for backup in backups.list_backups()])
backups.prune_backups()
```

`BackupConfig` validates on construction. A missing `name` or storage location
raises `pydantic.ValidationError`. Every field is in the [configuration
reference](../reference/configuration.md).

## The ezbak() shortcut

For quick scripts, `ezbak(**kwargs)` builds the config for you. These two lines
are equivalent:

```python
from ezbak import EZBak, BackupConfig, ezbak

backups = ezbak(name="my-backup", source_paths=["/data"], storage_paths=["/backups"])
backups = EZBak(BackupConfig(name="my-backup", source_paths=["/data"], storage_paths=["/backups"]))
```

Prefer `EZBak(BackupConfig(...))` when you want an explicit, reusable config.

## Restore

`restore_backup()` restores the latest backup by default. Pass a `restore_path`,
or set it on the config.

```python
backups.restore_backup(restore_path="/restore")
```

To restore an older backup, select it with `get_backup_as_of()` and pass it in:

```python
backup = backups.get_backup_as_of("20241201")
if backup:
    backups.restore_backup(restore_path="/restore", backup=backup)
```

`get_backup_as_of(point_in_time)` returns the newest backup at or before the end
of the period you name. An explicit `backup` argument beats a configured
`restore_date`, which beats the latest backup.

## Preview a prune

`prune_backups(dry_run=True)` returns the backups the retention policy would
delete, without removing any:

```python
would_delete = backups.prune_backups(dry_run=True)
print(f"Would delete {len(would_delete)} backups")
```

## Handle failures

Every ezbak error subclasses `EZBakError`. A backup that cannot write to a
storage location raises `BackupFailedError`, which still keeps the copies that
succeeded.

```python
from ezbak.exceptions import BackupFailedError

try:
    backups.create_backup()
except BackupFailedError as error:
    print(f"Failed storage locations: {error.failed_storage_locations}")
    print(f"Backups that succeeded: {[b.name for b in error.created_backups]}")
```

A restore that cannot download or extract an archive raises `RestoreFailedError`:

```python
from ezbak.exceptions import RestoreFailedError

try:
    backups.restore_backup(restore_path="/restore")
except RestoreFailedError as error:
    print(f"Restore failed: {error}")
```

`restore_backup()` returns `False`, and raises nothing, when there is simply no
backup to restore. A library caller checks the return value and decides what to
do, so the `restore_if_exists` option is only for the CLI and container. See
[Failure behavior](../concepts/failure-behavior.md) and the full [Python API
reference](../reference/python-api.md).
