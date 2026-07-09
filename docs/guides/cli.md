# Using the CLI

The `ezbak` command runs backups from a shell. It shares its configuration with
the library and container, so anything you can do in a container you can do at the
terminal for a one-off backup, a local test, or a scripted job.

## Command shape

The `name` and `storage` options are global and come before the subcommand.
Everything else belongs to a subcommand.

```bash
ezbak --name my-documents --storage ~/Backups <command> [options]
```

Run `ezbak --help` or `ezbak <command> --help` for the full list. The four
commands are `create`, `list`, `prune`, and `restore`.

## Create a backup

```bash
ezbak --name my-documents --storage ~/Backups create --source ~/Documents
```

Add more sources by repeating `--source`. Narrow the file selection with
`--include-regex` and `--exclude-regex`. See
[Including and excluding files](../concepts/filtering.md).

## List backups

```bash
ezbak --name my-documents --storage ~/Backups list
```

Each line shows the full `YYYYMMDDTHHMMSS` timestamp. Copy one into
`restore --restore-date` to restore that exact backup.

## Prune old backups

Set one or more keep rules; a backup survives if any rule marks it. Preview
first with `--dry-run`.

```bash
# Keep the 10 most recent
ezbak --name my-documents --storage ~/Backups prune --keep-last 10

# See what a prune would remove, without removing it
ezbak --name my-documents --storage ~/Backups prune --keep-last 10 --dry-run
```

See [Retention policies](retention.md) for how the rules combine.

## Restore a backup

```bash
# Latest backup
ezbak --name my-documents --storage ~/Backups restore --restore-path ~/restore

# Newest backup at or before a point in time
ezbak --name my-documents --storage ~/Backups \
  restore --restore-path ~/restore --restore-date 202412

# Exit cleanly if no backup exists yet
ezbak --name my-documents --storage ~/Backups \
  restore --restore-path ~/restore --if-exists
```

See [Restore backups](restore.md) for the point-in-time matching rule and
`--if-exists`.

## Back up to S3

Pass `--s3-bucket` and provide credentials through the environment. The CLI has
no credential flags, so secrets never land in your shell history.

```bash
export EZBAK_AWS_ACCESS_KEY="your-access-key"
export EZBAK_AWS_SECRET_KEY="your-secret-key"

ezbak --name my-documents --storage ~/Backups --s3-bucket my-bucket \
  create --source ~/Documents
```

See [Back up to S3](s3.md) for bucket prefixes and writing to local and S3 at
once.

## Verbosity

Add `-v` for `DEBUG` output or `-vv` for `TRACE`. Write logs to a file with
`--log-file`, and prefix every line with `--log-prefix`.

```bash
ezbak -vv --name my-documents --storage ~/Backups --log-file ezbak.log \
  create --source ~/Documents
```

For every flag, see the [CLI reference](../reference/cli.md).
