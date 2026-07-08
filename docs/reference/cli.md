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
| `--compression-level` | `-c` | gzip level, 1 to 9. | `9` |

```bash
ezbak --name my-documents --storage ~/Backups create --source ~/Documents
```

## list

List every backup in the configured storage locations, oldest to newest. The
command takes no options beyond the global ones.

```bash
ezbak --name my-documents --storage ~/Backups list
```

Each line includes the full `YYYYMMDDTHHMMSS` timestamp, which you can pass back
to `restore --restore-date` to restore that exact backup.

## prune

Delete old backups according to a retention policy. Set a count with
`--max-backups`, or set one or more time-based limits. The two policies cannot
combine: `--max-backups` wins and the time-based options are ignored.

| Option | Short | Description | Default |
| --- | --- | --- | --- |
| `--max-backups` | `-x` | Keep this many of the most recent backups. | |
| `--yearly` | `-Y` | Yearly backups to keep. | |
| `--monthly` | `-M` | Monthly backups to keep. | |
| `--weekly` | `-W` | Weekly backups to keep. | |
| `--daily` | `-D` | Daily backups to keep. | |
| `--hourly` | `-H` | Hourly backups to keep. | |
| `--minutely` | `-S` | Minutely backups to keep. | |
| `--dry-run` | | List what would be deleted without deleting. | `False` |

```bash
# Keep the 10 most recent
ezbak --name my-documents --storage ~/Backups prune --max-backups 10

# Preview only
ezbak --name my-documents --storage ~/Backups prune --max-backups 10 --dry-run
```

## restore

Restore a backup into a target directory. Restores the latest backup unless you
name a point in time or use `--if-exists`.

| Option | Short | Description | Default |
| --- | --- | --- | --- |
| `--restore-path` | `-d` | Directory to restore into. Required. | |
| `--restore-date` | `-t` | Restore the newest backup at or before this time. | |
| `--clean-before-restore` | | Empty the restore path before extracting. | `False` |
| `--if-exists` | | Exit cleanly instead of failing when no backup exists. | `False` |
| `--uid` | `-u` | Set owner UID on restored files. | |
| `--gid` | `-g` | Set owner GID on restored files. | |

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
| `0` | The command succeeded. A restore with `--if-exists` and no backup also exits `0`. |
| `1` | The command failed: invalid configuration, a storage location that could not be used, or a restore that could not download or extract an archive. |
