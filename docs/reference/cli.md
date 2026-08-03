---
icon: lucide/square-terminal
---

# CLI reference

The `ezbak` command wraps the same configuration that the library and the
container use. Global options come before the subcommand. Each subcommand adds
its own options. To see everything at the terminal, run `ezbak --help` or
`ezbak <command> --help`.

```
ezbak [GLOBAL OPTIONS] <command> [COMMAND OPTIONS]
```

The four commands are `create`, `list`, `prune`, and `restore`.

## Global options

These apply to every command and come before the subcommand name.

| Option | Short | Description | Default |
| --- | --- | --- | --- |
| `--name` | `-n` | Name for the backup set. Required. | |
| `--storage` | | Local storage directory. Repeat for multiple. Optional when `--s3-bucket` is set. | |
| `--s3-bucket` | | S3 bucket name. | |
| `--s3-bucket-prefix` | | Key prefix within the bucket. | |
| `--s3-region` | | AWS region. Defaults to the standard resolution of boto3. | |
| `--s3-endpoint-url` | | Custom S3 endpoint for S3-compatible storage such as MinIO. | |
| `--log-file` | | Also write the logs to this file. | |
| `--log-prefix` | | Prefix added to every log line. | |
| `-v` / `-vv` | | Raise verbosity to `DEBUG` (`-v`) or `TRACE` (`-vv`). | `INFO` |

!!! note "No CLI flag for S3 credentials"

    The CLI has no flag for AWS credentials. Set `EZBAK_AWS_ACCESS_KEY` and
    `EZBAK_AWS_SECRET_KEY` in the environment, so secrets never pass through the
    command line. Leave both unset to use an instance role, or any other
    credential source boto3 can find. See [Back up to S3](../guides/s3.md).

## create

Create a backup archive of one or more sources.

| Option | Short | Description | Default |
| --- | --- | --- | --- |
| `--source` | | Source path to back up. Repeat for multiple. Required. | |
| `--include-regex` | `-i` | Back up only the files whose path matches this regular expression. | |
| `--exclude-regex` | `-e` | Skip the files whose path matches this regular expression. | |
| `--strip-source-paths` | `-s` | Flatten directory sources in the archive. | `False` |
| `--sqlite-path` | | Path or glob pattern that matches SQLite databases inside a source path, to snapshot consistently instead of copying. Repeat for multiple entries. See [Match databases with a pattern](../concepts/sqlite.md#match-databases-with-a-pattern). | |
| `--compression-level` | `-c` | gzip level, 1 to 9. | `6` |
| `--use-checksums` / `--no-use-checksums` | | Write a `.sha256` checksum file for each backup, and verify it on restore. | `True` |

```bash
ezbak --name my-documents --storage ~/Backups create --source ~/Documents
```

## list

List the backups in the configured storage locations, grouped into a local set
and an S3 set. The command takes no options beyond the global ones.

```bash
ezbak --name my-documents --storage ~/Backups list
```

Each entry includes the full `YYYYMMDDTHHMMSS` timestamp. A local backup prints
the full path, and an S3 backup prints the object name. Pass that timestamp to
`restore --restore-date` to restore that exact backup.

If a configured storage location cannot be read, `list` prints the backups it did
find, then names the unreadable locations and exits non-zero. It does not report
that no backups exist. A location that is readable and genuinely holds no backups
still prints "No backups found" and exits `0`. See [An unreadable storage
location is not an empty
one](../concepts/failure-behavior.md#an-unreadable-storage-location-is-not-an-empty-one).

## prune

Delete old backups according to your keep rules. Set one or more. If any rule
marks a backup, that backup survives, so the rules compose instead of forcing a
single choice.

| Option | Short | Description | Default |
| --- | --- | --- | --- |
| `--keep-last` | | Keep this many of the most recent backups. | |
| `--keep-yearly` | `-Y` | Yearly backups to keep. | |
| `--keep-monthly` | `-M` | Monthly backups to keep. | |
| `--keep-weekly` | `-W` | Weekly backups to keep. | |
| `--keep-daily` | `-D` | Daily backups to keep. | |
| `--keep-hourly` | `-H` | Hourly backups to keep. | |
| `--keep-minutely` | `-S` | Minutely backups to keep. | |
| `--dry-run` | | List what a prune deletes, and delete nothing. | `False` |
| `--force` | | Skip the confirmation prompt and prune immediately. | `False` |

If a configured storage location cannot be read, `prune` skips it, logs an error,
leaves its archives untouched, and exits `0`. The prune still completes on every
location that is reachable. Unlike `list`, an unreadable location does not change
the exit code of a prune. See [An unreadable storage location is not an empty
one](../concepts/failure-behavior.md#an-unreadable-storage-location-is-not-an-empty-one).

```bash
# Keep the 10 most recent
ezbak --name my-documents --storage ~/Backups prune --keep-last 10

# Preview only
ezbak --name my-documents --storage ~/Backups prune --keep-last 10 --dry-run
```

A prune asks for confirmation before it deletes anything. Add `--force` to skip
the prompt in a non-interactive script. `--dry-run` skips the prompt too, because
it deletes nothing.

```bash
# Prune without the confirmation prompt
ezbak --name my-documents --storage ~/Backups prune --keep-last 10 --force
```

## restore

Restore a backup into a target directory. The command restores the latest backup
unless you name a point in time or use `--skip-if-no-backup`.

| Option | Short | Description | Default |
| --- | --- | --- | --- |
| `--restore-path` | `-d` | Directory to restore into. Required. | |
| `--restore-date` | `-t` | Restore the newest backup at or before this time. | |
| `--clean-before-restore` | | Empty the restore path as part of the restore. Refuses to target a storage location. | `False` |
| `--skip-if-no-backup` | | Exit cleanly instead of failing when no backup exists. | `False` |
| `--skip-if-populated` | | Skip the restore, as success, when the target already holds data. `--clean-before-restore` bypasses this. | `False` |
| `--uid` | `-u` | Set owner UID on the restored files. | |
| `--gid` | `-g` | Set owner GID on the restored files. | |
| `--use-checksums` / `--no-use-checksums` | | Verify the archive against its `.sha256` checksum file on restore. | `True` |

```bash
# Restore the latest backup
ezbak --name my-documents --storage ~/Backups restore --restore-path ~/restore

# Restore the last backup from December 2024
ezbak --name my-documents --storage ~/Backups \
  restore --restore-path ~/restore --restore-date 202412
```

`--restore-date` accepts six formats, from a year down to a second: `YYYY`,
`YYYYMM`, `YYYYMMDD`, `YYYYMMDDTHH`, `YYYYMMDDTHHMM`, and `YYYYMMDDTHHMMSS`. It
restores the newest backup at or before the end of the period you name, not the
one closest to it. For the matching rule, see
[Restore backups](../guides/restore.md).

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The command succeeded. A restore with `--skip-if-no-backup` and no backup, or with `--skip-if-populated` and a populated target, also exits `0`. |
| `1` | The command failed: an invalid configuration, a storage location that ezbak cannot use or read, or a restore that cannot download or extract an archive. `prune` is the exception. It skips a location it cannot read and still exits `0`. |
