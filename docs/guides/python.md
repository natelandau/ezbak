---
icon: lucide/code
---

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
        keep_last=10,
    )
)

backups.create_backup()
print([backup.name for backup in backups.list_backups()])
backups.prune_backups()
```

`BackupConfig` validates on construction. A missing `name` or storage location
raises `pydantic.ValidationError`. Every field is in the
[configuration reference](../reference/configuration.md).

## The ezbak() shortcut

For quick scripts, `ezbak(**kwargs)` builds the configuration for you. These two
lines are equivalent:

```python
from ezbak import EZBak, BackupConfig, ezbak

backups = ezbak(name="my-backup", source_paths=["/data"], storage_paths=["/backups"])
backups = EZBak(BackupConfig(name="my-backup", source_paths=["/data"], storage_paths=["/backups"]))
```

When you want an explicit, reusable configuration, prefer
`EZBak(BackupConfig(...))`.

## Restore

`restore_backup()` restores the latest backup by default. Pass a `restore_path`,
or set it on the configuration.

```python
backups.restore_backup(restore_path="/restore")
```

To restore an older backup, select it with `get_backup_as_of()` and pass it to
`restore_backup()`:

```python
backup = backups.get_backup_as_of("20241201")
if backup:
    backups.restore_backup(restore_path="/restore", backup=backup)
```

`get_backup_as_of(point_in_time)` returns the newest backup at or before the end
of the period you name. An explicit `backup` argument takes priority over a
configured `restore_date`, which in turn takes priority over the latest backup.

## Preview a prune

`prune_backups(dry_run=True)` returns the backups that the keep rules no longer
keep. It deletes none of them:

```python
would_delete = backups.prune_backups(dry_run=True)
print(f"Would delete {len(would_delete)} backups")
```

## Handle failures

Every ezbak error subclasses `EZBakError`. A backup that cannot write to a
storage location raises `BackupFailedError`, and it still keeps the copies that
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

`restore_backup()` returns a `RestoreOutcome` member and raises nothing for two
cases that are not errors. It returns `RestoreOutcome.NO_BACKUP` when there is no
backup to restore. It returns `RestoreOutcome.SKIPPED_POPULATED` when
`skip_restore_if_populated` is set and the target already holds data.

A library caller reads the return value and decides what to do.
`skip_if_no_backup` and `skip_restore_if_populated` therefore matter mainly to
the CLI and the container, which turn those results into an exit code. See
[Failure behavior](../concepts/failure-behavior.md) and the full
[Python API reference](../reference/python-api.md).
