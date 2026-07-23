---
icon: lucide/square-terminal
---

# CLI reference

The `ezbak` command wraps the same configuration the library and container use.
Global options come before the subcommand; each subcommand adds its own options.
Run `ezbak --help` or `ezbak <command> --help` to see everything at the terminal.

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
| `--s3-region` | | AWS region; defaults to boto3's standard resolution. | |
| `--s3-endpoint-url` | | Custom S3 endpoint for S3-compatible storage such as MinIO. | |
| `--log-file` | | Also write logs to this file. | |
| `--log-prefix` | | Prefix added to every log line. | |
| `-v` / `-vv` | | Raise verbosity to `DEBUG` (`-v`) or `TRACE` (`-vv`). | `INFO` |

!!! note "S3 credentials come from the environment"

    The CLI has no flag for AWS credentials. Set `EZBAK_AWS_ACCESS_KEY` and
    `EZBAK_AWS_SECRET_KEY` in the environment so secrets never pass through
    `argv`. See [Back up to S3](../guides/s3.md).

## create

Create a backup archive of one or more sources.

| Option | Short | Description | Default |
| --- | --- | --- | --- |
| `--source` | | Source path to back up. Repeat for multiple. Required. | |
| `--include-regex` | `-i` | Back up only files whose path matches this regex. | |
| `--exclude-regex` | `-e` | Skip files whose path matches this regex. | |
| `--strip-source-paths` | `-s` | Flatten directory sources in the archive. | `False` |
| `--compression-level` | `-c` | gzip level, 1 to 9. | `6` |
| `--use-checksums` / `--no-use-checksums` | | Write a `.sha256` sidecar for each backup and verify it on restore. | `True` |

```bash
ezbak --name my-documents --storage ~/Backups create --source ~/Documents
```

## list

List every backup in the configured storage locations, oldest to newest. The
command takes no options beyond the global ones.

```bash
ezbak --name my-documents --storage ~/Backups list
```

Each line shows the backup's filename, which includes the full
`YYYYMMDDTHHMMSS` timestamp. Pass that timestamp to `restore --restore-date` to
restore that exact backup.

## prune

Delete old backups according to your keep rules. Set one or more; a backup
survives if any rule marks it, so the rules compose rather than forcing a single
choice.

| Option | Short | Description | Default |
| --- | --- | --- | --- |
| `--keep-last` | | Keep this many of the most recent backups. | |
| `--keep-yearly` | `-Y` | Yearly backups to keep. | |
| `--keep-monthly` | `-M` | Monthly backups to keep. | |
| `--keep-weekly` | `-W` | Weekly backups to keep. | |
| `--keep-daily` | `-D` | Daily backups to keep. | |
| `--keep-hourly` | `-H` | Hourly backups to keep. | |
| `--keep-minutely` | `-S` | Minutely backups to keep. | |
| `--dry-run` | | List what would be deleted without deleting. | `False` |
| `--force` | | Skip the confirmation prompt and prune immediately. | `False` |

```bash
# Keep the 10 most recent
ezbak --name my-documents --storage ~/Backups prune --keep-last 10

# Preview only
ezbak --name my-documents --storage ~/Backups prune --keep-last 10 --dry-run
```

Prune asks for confirmation before it deletes. Add `--force` to skip the prompt
in a non-interactive script; `--dry-run` skips it too, since it deletes nothing.

```bash
# Prune without the confirmation prompt
ezbak --name my-documents --storage ~/Backups prune --keep-last 10 --force
```

## restore

Restore a backup into a target directory. Restores the latest backup unless you
name a point in time or use `--skip-if-no-backup`.

| Option | Short | Description | Default |
| --- | --- | --- | --- |
| `--restore-path` | `-d` | Directory to restore into. Required. | |
| `--restore-date` | `-t` | Restore the newest backup at or before this time. | |
| `--clean-before-restore` | | Empty the restore path as part of the restore. Refuses to target a storage location. | `False` |
| `--skip-if-no-backup` | | Exit cleanly instead of failing when no backup exists. | `False` |
| `--skip-if-populated` | | Skip the restore, as success, when the target already contains data. `--clean-before-restore` bypasses this. | `False` |
| `--uid` | `-u` | Set owner UID on restored files. | |
| `--gid` | `-g` | Set owner GID on restored files. | |
| `--use-checksums` / `--no-use-checksums` | | Verify the archive against its `.sha256` sidecar on restore. | `True` |

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
one closest to it. See [Restore backups](../guides/restore.md) for the matching
rule.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The command succeeded. A restore with `--skip-if-no-backup` and no backup, or with `--skip-if-populated` and a populated target, also exits `0`. |
| `1` | The command failed: invalid configuration, a storage location that could not be used, or a restore that could not download or extract an archive. |
