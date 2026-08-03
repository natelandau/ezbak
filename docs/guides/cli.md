---
icon: lucide/terminal
---

# Using the CLI

The `ezbak` command runs backups from a shell. It shares its configuration with
the library and the container. Anything you can do in a container, you can do at
the terminal for a one-off backup, a local test, or a scripted job.

## Command shape

The global options come before the subcommand. They include `--name`,
`--storage`, the `--s3-*` options, `-v`/`-vv`, `--log-file`, and `--log-prefix`.
The options of each subcommand come after the subcommand.

```bash
ezbak --name my-documents --storage ~/Backups <command> [options]
```

For the full list, run `ezbak --help` or `ezbak <command> --help`. The four
commands are `create`, `list`, `prune`, and `restore`.

## Create a backup

```bash
ezbak --name my-documents --storage ~/Backups create --source ~/Documents
```

To add more sources, repeat `--source`. To narrow the file selection, use
`--include-regex` and `--exclude-regex`. See
[Including and excluding files](../concepts/filtering.md).

## List backups

```bash
ezbak --name my-documents --storage ~/Backups list
```

Each line prints the filename of the backup, which includes the full
`YYYYMMDDTHHMMSS` timestamp. Copy that timestamp into `restore --restore-date` to
restore that exact backup.

## Prune old backups

Set one or more keep rules. If any rule marks a backup, that backup survives.
Preview the result first with `--dry-run`.

```bash
# Keep the 10 most recent
ezbak --name my-documents --storage ~/Backups prune --keep-last 10

# See what a prune deletes, without deleting it
ezbak --name my-documents --storage ~/Backups prune --keep-last 10 --dry-run
```

A prune asks for confirmation before it deletes anything. In a script, or in any
other non-interactive context, add `--force` to skip the prompt. `--dry-run`
skips the prompt too, because it deletes nothing.

```bash
ezbak --name my-documents --storage ~/Backups prune --keep-last 10 --force
```

For how the rules combine, see
[Retention policies](../concepts/retention.md).

## Restore a backup

```bash
# Latest backup
ezbak --name my-documents --storage ~/Backups restore --restore-path ~/restore

# Newest backup at or before a point in time
ezbak --name my-documents --storage ~/Backups \
  restore --restore-path ~/restore --restore-date 202412

# Exit cleanly if no backup exists yet
ezbak --name my-documents --storage ~/Backups \
  restore --restore-path ~/restore --skip-if-no-backup
```

For the point-in-time matching rule and `--skip-if-no-backup`, see
[Restore backups](restore.md).

## Back up to S3

Pass `--s3-bucket`. If the host has no credentials of its own, provide them
through the environment. The CLI has no credential flags, so secrets never land
in your shell history.

```bash
export EZBAK_AWS_ACCESS_KEY="your-access-key"
export EZBAK_AWS_SECRET_KEY="your-secret-key"

ezbak --name my-documents --storage ~/Backups --s3-bucket my-bucket \
  create --source ~/Documents
```

For bucket prefixes, and for writing to local storage and S3 at once, see
[Back up to S3](s3.md).

## Verbosity

Add `-v` for `DEBUG` output, or `-vv` for `TRACE`. Write logs to a file with
`--log-file`. Add a prefix to every line with `--log-prefix`.

```bash
ezbak -vv --name my-documents --storage ~/Backups --log-file ezbak.log \
  create --source ~/Documents
```

For every flag, see the [CLI reference](../reference/cli.md).
