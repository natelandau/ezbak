---
icon: lucide/rotate-ccw
---

# Restore backups

A restore extracts a backup archive into a target directory. By default ezbak
restores the latest backup. You can restore an older one by naming a point in
time, empty the target first, and set ownership on the restored files.

## Restore the latest backup

=== "Container"

    ```bash
    docker run -it \
        -v /path/to/backups:/backups:ro \
        -v /path/to/restore:/restore \
        -e EZBAK_ACTION=restore \
        -e EZBAK_NAME=my-backup \
        -e EZBAK_STORAGE_PATHS=/backups \
        -e EZBAK_RESTORE_PATH=/restore \
        ghcr.io/natelandau/ezbak:latest
    ```

=== "CLI"

    ```bash
    ezbak --name my-backup --storage ~/Backups restore --restore-path ~/restore
    ```

=== "Python"

    ```python
    backups.restore_backup(restore_path="/restore")
    ```

!!! note "Restores verify a checksum sidecar by default"

    With `use_checksums` enabled (the default), ezbak checks the archive against
    its `.sha256` sidecar while extracting it and fails the restore before your
    data is touched if they differ. A missing or unreadable sidecar logs a
    warning and restores anyway. Set `use_checksums` to `false` (or pass
    `--no-use-checksums`) to skip the check and ignore any sidecar. See [Archive
    integrity checksums](../concepts/checksums.md).

## Restore a backup from a point in time

Set a restore date to recover the state as of an earlier moment. ezbak restores
the newest backup at or before the **end** of the period you name, not the backup
closest to it.

```bash
# The last backup from December 2024, even if it landed on December 30
ezbak --name my-backup --storage ~/Backups \
  restore --restore-path ~/restore --restore-date 202412
```

The date accepts six granularities, from a year down to a second:

| Format | Example | Restores the newest backup at or before |
| --- | --- | --- |
| `YYYY` | `2024` | the end of 2024 |
| `YYYYMM` | `202412` | the end of December 2024 |
| `YYYYMMDD` | `20241215` | the end of December 15, 2024 |
| `YYYYMMDDTHH` | `20241215T14` | the end of the 14:00 hour |
| `YYYYMMDDTHHMM` | `20241215T1430` | the end of the 14:30 minute |
| `YYYYMMDDTHHMMSS` | `20241215T143022` | that exact second |

The full `YYYYMMDDTHHMMSS` form matches the timestamp in each filename the `list`
command prints, so you can copy that timestamp from a `list` entry to restore
that exact backup.

!!! note "A restore date that matches nothing fails, it does not fall back"

    If a restore date resolves to no backup, ezbak reports that no backup was
    found rather than silently restoring the latest. Restoring newer data than
    you asked for would be the wrong result. Combine it with `--if-exists` to turn
    a miss into a clean no-op instead of a failure.

## Empty the target before restoring

`clean_before_restore` removes the existing contents of the restore path, so the
result matches the backup exactly with no leftover files.

```bash
ezbak --name my-backup --storage ~/Backups \
  restore --restore-path ~/restore --clean-before-restore
```

ezbak extracts the archive into a staging directory inside the restore path and
swaps it into place only after the extract succeeds. The target is emptied at
that last step, so a download or extract failure leaves the existing contents
intact instead of deleting them first. See [Failure
behavior](../concepts/failure-behavior.md).

!!! warning "A clean restore refuses to target a storage location"

    ezbak rejects a clean restore whose path is, or contains, one of your
    `--storage` locations, because emptying it would delete the backups. Restoring
    into a subdirectory of a storage location is still allowed. The check compares
    the real directories, so it also catches two container mounts that point at the
    same host path.

## Set ownership on restored files

`--uid` and `--gid` set the owner and group on the restored files, which is useful
when restoring into a volume a service reads as a specific user. Set both.

```bash
ezbak --name my-backup --storage ~/Backups \
  restore --restore-path ~/restore --uid 1000 --gid 1000
```

## Skip cleanly when no backup exists

`--if-exists` (`EZBAK_RESTORE_IF_EXISTS`) turns a missing backup into a clean
no-op that exits zero, instead of a failure. This is what lets a pre-start restore
run on a fresh deployment with no backup yet.

```bash
ezbak --name my-backup --storage ~/Backups \
  restore --restore-path ~/restore --if-exists
```

A real download or extract failure still fails, with or without `--if-exists`.
See [Fresh deploys](../orchestration/fresh-deploys.md) for the orchestration case.

A library caller does not need this option: `restore_backup()` returns `False`
when there is nothing to restore, and the caller decides how to react.
