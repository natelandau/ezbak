---
icon: lucide/wrench
---

# Troubleshooting

Common problems and what causes them. If your issue is not here, run with `-vv`
(CLI) or set `EZBAK_LOG_LEVEL=TRACE` (container) for the most detailed logs.

## A restore reports no backup to restore

ezbak found no backup matching the name and, if set, the restore date. This is not
a failure in itself.

- Confirm `EZBAK_NAME` (or `--name`) matches the name the backups were created
  with. The name groups a backup set, so a mismatch finds nothing.
- Confirm the storage location holds backups for that name. Run `list` to check.
- If you set a restore date, confirm a backup exists at or before it. A date that
  matches nothing reports no backup rather than falling back to the latest. See
  [Restore backups](guides/restore.md).

On a fresh deployment with no backup yet, this is expected. Set
`EZBAK_SKIP_IF_NO_BACKUP=true` (CLI `--skip-if-no-backup`) so it exits cleanly. See
[Fresh deploys](orchestration/fresh-deploys.md).

## A backup fails with bad S3 credentials or an unreachable bucket

ezbak validates each storage location before reporting success, so a bad bucket or
credential fails the run.

- Check `EZBAK_AWS_ACCESS_KEY` and `EZBAK_AWS_SECRET_KEY` are set in the
  environment. The CLI never takes credentials as flags.
- Check `EZBAK_AWS_S3_BUCKET_NAME` names a bucket the credentials can reach.
- If you configured both local and S3 storage, the local copy still succeeds; only
  the S3 write fails. See [Failure behavior](concepts/failure-behavior.md).

## Backup timestamps are in the wrong timezone

Timestamps use ezbak's configured timezone. When none is set, ezbak falls back to
the system timezone, and the container image sets that to `Etc/UTC`, so an
unconfigured container stamps timestamps in UTC.

- In a container, set `TZ` to your IANA timezone, for example
  `TZ=America/New_York`.
- To set ezbak's timezone directly and override the system one, set `EZBAK_TZ`.

See [Backup names](concepts/backup-names.md).

## A scheduled backup failed but the container is still running

This is by design. A scheduled run logs its error and keeps the container up so the
next run retries. It does not crash the container.

!!! bug "Scheduled failures need a monitor to be noticed"

    A scheduled job's own exceptions travel through Python's standard logging,
    which is separate from ezbak's log sink. ezbak catches those exceptions and
    re-logs them so they stay visible, then pings the failure endpoint. Set
    `EZBAK_HEALTHCHECK_URL` so you are alerted when a scheduled run fails or stops
    happening. See [Monitoring](orchestration/monitoring.md).

## The container exits immediately with no backup

Two configuration gaps cause an immediate exit:

- No action. Set `EZBAK_ACTION` to `backup` or `restore`. Without it, the
  container logs an error and exits non-zero.
- No storage location. Set `EZBAK_STORAGE_PATHS`, `EZBAK_AWS_S3_BUCKET_NAME`, or
  both. A configuration with neither fails validation.

A bad configuration logs a clear message and exits non-zero rather than printing a
traceback.

## Running the container locally picks up real credentials

The container reads `.env` and `.env.secrets` from its working directory. On a
development machine, that can load real S3 credentials into a test run. Keep those
files out of any directory you mount into a test container. See
[Environment variables](reference/environment-variables.md).

## An expected file is missing from a backup

ezbak always skips a set of noise files, and your regex options may exclude more.

- The always-excluded names are `.DS_Store`, `@eaDir`, `.Trashes`, `__pycache__`,
  `Thumbs.db`, and `IconCache.db`.
- Check `include_regex`: if set, only matching files are backed up.
- Check `exclude_regex`: matching files are skipped.

See [Including and excluding files](concepts/filtering.md).
