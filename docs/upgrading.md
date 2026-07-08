# Upgrade guide

v1.0.0 is the first stable release of ezbak. Coming from v0.12.4, it renames
several options, removes a few, restructures the Python library, and makes failed
runs exit loudly instead of reporting success. This guide lists every change that
affects an existing setup and what to do about each one.

!!! warning "A renamed environment variable is silently ignored"

    ezbak ignores unknown `EZBAK_` variables rather than erroring on them. If you
    leave a renamed variable at its old name, the setting is dropped without a
    message and the option falls back to its default. Update the names below
    before you upgrade, or a backup could run with retention, storage, or cleanup
    behavior you did not intend.

## What you must change

| Area | Change |
| --- | --- |
| Environment variables | Two renamed, three removed. |
| CLI flags | Two renamed on `restore`, one flag removed on `create`. |
| Python library | Class renamed, construction changed, one method and one exception attribute renamed. |
| Behavior | Failed backups, restores, and prunes now exit non-zero. |

Your existing backup files need no changes. See [Backup files stay
compatible](#backup-files-stay-compatible).

## Renamed environment variables

Both the container and the CLI credential fallback read these. Rename them
wherever you set ezbak's environment.

| Old (v0.12.4) | New (v1.0.0) |
| --- | --- |
| `EZBAK_AWS_S3_BUCKET_PATH` | `EZBAK_AWS_S3_BUCKET_PREFIX` |
| `EZBAK_DELETE_SRC_AFTER_BACKUP` | `EZBAK_DELETE_SOURCE_AFTER_BACKUP` |

The library config fields and the CLI flag changed to match: `aws_s3_bucket_path`
became `aws_s3_bucket_prefix`, exposed on the command line as the new
`--s3-bucket-prefix` flag.

## Renamed CLI flags

The `restore` command renamed two flags. The short form `-d` is unchanged.

| Old (v0.12.4) | New (v1.0.0) |
| --- | --- |
| `restore --destination` | `restore --restore-path` |
| `restore --clean` | `restore --clean-before-restore` |

The rename retires "destination" as a user-facing word, which previously meant
both where backups are stored and where a restore writes. Storage is now
"storage" and the restore target is the "restore path". See the [CLI
reference](reference/cli.md).

## Removed options

Three options no longer exist. A leftover environment variable for any of them is
ignored; remove the setting to keep your configuration honest.

| Removed | Replacement |
| --- | --- |
| `storage_type` / `EZBAK_STORAGE_TYPE` | None needed. Backends follow the storage locations you set. |
| `label_time_units` / `EZBAK_LABEL_TIME_UNITS` / `create --no-label` | None. Filenames no longer carry a time-unit label. |
| `rename_files` / `EZBAK_RENAME_FILES` | None. The rename subsystem and the `rename_backups()` method are gone. |

`storage_type` is unnecessary because ezbak now derives its backends from the
destinations you configure: `storage_paths` gives local storage,
`aws_s3_bucket_name` gives S3, and setting both writes to both. See [Storage
locations](concepts/storage-locations.md).

## Backup files stay compatible

v1.0.0 changes the filename format: backups are now named `{name}-{timestamp}.tgz`
without the `-daily`, `-weekly`, and similar time-unit labels. This does not break
your existing backups.

!!! success "Old backups are still found, pruned, and restored"

    ezbak matches backups by name and timestamp, not by the old label. Backups
    written by v0.12.4 are discovered, pruned, and restored by v1.0.0 with no
    migration step. New backups simply use the shorter name. See [Backup
    names](concepts/backup-names.md).

## Python library changes

The library was restructured around one typed config schema and one class.

- The import changed: `from ezbak import EZBakApp, ezbak` becomes
  `from ezbak import EZBak, BackupConfig, ezbak`.
- The class `EZBakApp` is now `EZBak`.
- Build an `EZBak` from a `BackupConfig`. The `ezbak(**kwargs)` shortcut still
  works for quick scripts.

=== "v0.12.4"

    ```python
    from ezbak import EZBakApp, ezbak

    backups = ezbak(
        name="my-backup",
        source_paths=["/data"],
        storage_paths=["/backups"],
        storage_type="local",          # removed in v1.0.0
        delete_src_after_backup=False,  # renamed in v1.0.0
    )
    ```

=== "v1.0.0"

    ```python
    from ezbak import EZBak, BackupConfig

    backups = EZBak(
        BackupConfig(
            name="my-backup",
            source_paths=["/data"],
            storage_paths=["/backups"],
            delete_source_after_backup=False,
        )
    )
    ```

Two more library changes to note:

- `EZBak` no longer has a `rename_backups()` method.
- `BackupFailedError.failed_destinations` is now
  `BackupFailedError.failed_storage_locations`.

The `ezbak()` factory dropped the `storage_type`, `label_time_units`, and
`aws_s3_bucket_path` keyword arguments and renamed `delete_src_after_backup` to
`delete_source_after_backup`. See the [Python API reference](reference/python-api.md).

## Failures are now loud

In v0.12.4, some failures reported success or returned `False`. v1.0.0 makes them
fail loudly, so a failed run is never mistaken for a good one. This is the change
most likely to surface in a script or an orchestrated deployment that ignored the
old behavior.

- A backup to an unusable storage location now raises `BackupFailedError`, and the
  `ezbak create` command and a one-shot container exit non-zero. Copies that
  reached a working location are still kept. A scheduled container logs the error
  and keeps running.
- A restore that cannot download or extract an archive now raises
  `RestoreFailedError` and exits non-zero, instead of quietly returning `False`.
- A prune that partially fails now reports the backups actually deleted, not the
  ones it targeted, and `prune_backups()` returns that confirmed list.
- An invalid configuration now logs a clear message and exits non-zero instead of
  printing a raw traceback.

!!! tip "Check the exit codes your automation depends on"

    If a script or job treated a non-zero ezbak exit as noise, review it before
    upgrading. A run that used to look successful may now correctly fail. See
    [Failure behavior](concepts/failure-behavior.md).

## New in v1.0.0

These additions need no migration, but they are worth adopting:

- Point-in-time restore recovers the newest backup at or before a date with
  `restore_date` (`EZBAK_RESTORE_DATE`, `restore --restore-date`). See [Restore
  backups](guides/restore.md).
- `restore_if_exists` (`EZBAK_RESTORE_IF_EXISTS`, `restore --if-exists`) turns a
  missing backup into a clean no-op, which lets a pre-start restore run on a fresh
  deployment. See [Fresh deploys](orchestration/fresh-deploys.md).
- `prune --dry-run` reports what a prune would delete without deleting it.
- `EZBAK_HEALTHCHECK_URL` pings a monitor after each scheduled run so a silent
  failure gets noticed. See [Monitoring](orchestration/monitoring.md).
