---
icon: lucide/rotate-ccw
---

# Restore backups

A restore extracts a backup archive into a target directory. By default ezbak
restores the latest backup. You can also restore an older backup by a point in
time, empty the target first, or set ownership on the restored files.

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

!!! note "Restores verify a checksum file by default"

    With `use_checksums` enabled (the default), ezbak verifies the archive
    against its `.sha256` file as it extracts. If the two digests differ, the
    restore fails before ezbak touches your data. A missing or unreadable
    checksum file logs a warning, and the restore runs anyway. To skip the
    verification and ignore any checksum file, set `use_checksums` to `false`, or
    pass `--no-use-checksums`. See
    [Archive integrity checksums](../concepts/checksums.md).

## Restore a backup from a point in time

Set a restore date to recover the state of an earlier moment. ezbak restores the
newest backup at or before the **end** of the period you name, not the backup
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

The full `YYYYMMDDTHHMMSS` form matches the timestamp in each filename that the
`list` command prints. Copy that timestamp from a `list` entry to restore that
exact backup.

!!! note "A restore date that matches nothing fails"

    If a restore date resolves to no backup, ezbak reports that it found no
    backup. It does not restore the latest backup instead, because newer data
    than you asked for is the wrong result. Add `--skip-if-no-backup` to turn a
    miss into a clean no-op instead of a failure.

!!! note "Restore is all-or-nothing across storage locations"

    With both `--storage` and `--s3-bucket` configured, ezbak has to read every
    location before it restores anything. One unreadable location fails the
    restore, even though the healthy one can serve it. A restore of an older
    archive, while a possibly newer location is unreadable, stages stale state
    without a word. See [An unreadable storage location is not an empty
    one](../concepts/failure-behavior.md#an-unreadable-storage-location-is-not-an-empty-one).

## Empty the target before restoring

`clean_before_restore` deletes the existing contents of the restore path, so the
result matches the backup exactly, with no leftover files.

```bash
ezbak --name my-backup --storage ~/Backups \
  restore --restore-path ~/restore --clean-before-restore
```

ezbak extracts the archive into a staging directory inside the restore path. It
swaps that directory into place only after the extract succeeds. ezbak empties
the target at that last step, so a failed download or extract leaves the existing
contents intact. See [Failure behavior](../concepts/failure-behavior.md).

!!! warning "A clean restore refuses to target a storage location"

    ezbak rejects a clean restore whose path is, or contains, one of your
    `--storage` locations, because emptying that path deletes the backups. A
    restore into a subdirectory of a storage location is still allowed. ezbak
    compares the real directories, so it also catches two container mounts that
    point at the same host path.

!!! note "A restore clears stale SQLite journals it finds"

    A restore that does not empty the target first writes over the files the
    archive contains and leaves everything else alone. When it restores a SQLite
    database, it also deletes a `-wal`, `-shm`, or
    `-journal` file already beside that database. There is one exception: a
    journal that the archive supplied itself.

    SQLite replays a journal it finds next to a database. One left over from an
    earlier deployment therefore rolls the restored database back to that older
    data, and the result still passes an integrity check. This applies to any
    SQLite database in a backup, whether or not you used
    [`sqlite_paths`](../concepts/sqlite.md).

    ezbak touches nothing else. Before ezbak deletes either one, the restored
    file has to carry the header of SQLite itself. A `-wal` or `-journal` has to
    carry the matching journal header. See [Stale journals at the
    restore
    target](../concepts/sqlite.md#stale-journals-at-the-restore-target-are-cleared).

## Set ownership on restored files

`--uid` and `--gid` set the owner and the group on the restored files. Use them
when you restore into a volume that a service reads as a specific user. Set both.

```bash
ezbak --name my-backup --storage ~/Backups \
  restore --restore-path ~/restore --uid 1000 --gid 1000
```

## Skip cleanly when no backup exists

`--skip-if-no-backup` (`EZBAK_SKIP_IF_NO_BACKUP`) turns a missing backup into a
clean no-op that exits zero, instead of a failure. This is what lets a pre-start
restore run on a fresh deployment that has no backup yet.

```bash
ezbak --name my-backup --storage ~/Backups \
  restore --restore-path ~/restore --skip-if-no-backup
```

A real download or extract failure still fails the restore, with or without
`--skip-if-no-backup`. So does a storage location ezbak cannot read.
`--skip-if-no-backup` covers only a location that is readable and genuinely
empty. For the orchestration case, see
[Fresh deploys](../orchestration/fresh-deploys.md).

A library caller does not need this option. `restore_backup()` returns
`RestoreOutcome.NO_BACKUP` when there is nothing to restore, and the caller
decides how to react.

## Skip the restore when the target already has data

`--skip-if-populated` (`EZBAK_SKIP_RESTORE_IF_POPULATED`) skips the restore when
the target directory already holds data, and treats the skip as success. Use it
on a pre-start restore that must not overlay live application state with an older
snapshot. When the data volume of the job already holds files, ezbak leaves them
alone instead of extracting on top of them. The files can come from a service
that already started, or from an orchestrator retry.

```bash
ezbak --name my-service --storage ~/Backups \
  restore --restore-path /data --skip-if-populated
```

!!! info "What counts as populated"

    ezbak ignores the same noise files it never backs up (`.DS_Store`, `@eaDir`,
    `.Trashes`, `__pycache__`, `Thumbs.db`, `IconCache.db`). It also ignores
    `lost+found`, which a fresh ext-filesystem mount holds, and its own
    `.ezbak-restore-*` staging directories. Only files beyond that list count as
    data. An empty target, or one that holds only that benign noise, still
    restores normally.

`clean_before_restore` bypasses this guard. A clean restore is an explicit
replace, so it always runs, even when the target is populated. Both options
together mean "wipe and restore, always".

A populated-target skip does not run the post-restore hook. ezbak wrote nothing,
so the hook has nothing to act on. See
[Container lifecycle hooks](hooks.md).

`--skip-if-populated` is independent of `--skip-if-no-backup`.
`--skip-if-no-backup` handles a *missing* backup. `--skip-if-populated` handles
an *already-occupied* target. Use either alone, or both together, most commonly
on a pre-start restore task. See
[Fresh deploys](../orchestration/fresh-deploys.md).

A library caller sets `skip_restore_if_populated=True` on `BackupConfig` and
reads the return value:

```python
from ezbak import BackupConfig, EZBak
from ezbak.constants import RestoreOutcome

backups = EZBak(BackupConfig(
    name="my-service",
    storage_paths=["/backups"],
    restore_path="/data",
    skip_restore_if_populated=True,
))

outcome = backups.restore_backup()
if outcome is RestoreOutcome.SKIPPED_POPULATED:
    print("Target already had data; left it alone")
```
