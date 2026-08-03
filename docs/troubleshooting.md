---
icon: lucide/wrench
---

# Troubleshooting

Common problems and what causes them. If your problem is not here, run with `-vv`
(CLI), or set `EZBAK_LOG_LEVEL=TRACE` (container), for the most detailed logs.

## A restore reports no backup to restore

ezbak found no backup that matches the name, and the restore date when you set
one. This is not a failure in itself.

- Make sure that `EZBAK_NAME` (or `--name`) matches the name the backups were
  created with. The name groups a backup set, so a mismatch finds nothing.
- Run `list` to make sure that the storage location holds backups for that name.
- If you set a restore date, make sure that a backup exists at or before it. A
  date that matches nothing reports no backup. It does not restore the latest
  backup instead. See [Restore backups](guides/restore.md).

On a fresh deployment with no backup yet, this is expected. Set
`EZBAK_SKIP_IF_NO_BACKUP=true` (CLI `--skip-if-no-backup`) so the restore exits
cleanly. See [Fresh deploys](orchestration/fresh-deploys.md).

A restore can also fail outright instead: a non-zero exit with a logged error,
rather than a clean no-op. Then ezbak cannot read the storage location at all,
because of a bad credential, an unreachable bucket, or a permission error.
`EZBAK_SKIP_IF_NO_BACKUP` does not cover that case. See [An unreadable storage
location is not an empty
one](concepts/failure-behavior.md#an-unreadable-storage-location-is-not-an-empty-one).

## A backup fails with bad S3 credentials or an unreachable bucket

ezbak validates each storage location before it reports success, so a bad bucket
or credential fails the run.

- Make sure that `EZBAK_AWS_ACCESS_KEY` and `EZBAK_AWS_SECRET_KEY` are both set
  in the environment, or both unset to use the credentials of the host. The CLI
  never takes credentials as flags.
- Make sure that `EZBAK_AWS_S3_BUCKET_NAME` names a bucket the credentials can
  reach.
- If you configured both local and S3 storage, the local copy still succeeds.
  Only the S3 write fails. See [Failure behavior](concepts/failure-behavior.md).

### S3 authentication fails on EC2 or in Kubernetes

- Run with `EZBAK_LOG_LEVEL=debug` and look for `S3 credentials resolved via
  '...'`. A provider of `none` means nothing resolved at all.
- Set both `EZBAK_AWS_ACCESS_KEY` and `EZBAK_AWS_SECRET_KEY`, or neither. A
  half-set pair fails at startup and names the missing option.
- On Docker with bridge networking, the instance metadata service is unreachable
  by default. The IMDSv2 hop limit of 1 stops the request of the container from
  reaching `169.254.169.254`. Raise `--http-put-response-hop-limit` on the
  instance to 2, or use host networking.
- The role needs `s3:ListBucket` on the bucket. ezbak calls `HeadBucket`, which
  that permission covers.
- ezbak retries a storage location that it cannot reach at startup, on every later
  run. A cron sidecar therefore recovers on its next scheduled backup, once the
  role attaches or the network comes up. You do not have to restart the
  container.

## A prune left old backups in one storage location

A prune skips a storage location it cannot read instead of pruning it, so backups
beyond your retention rules can remain there. This does not fail the prune or
change its exit code. Read the logs for the error that names the skipped
location, then correct the same credential or connectivity problem described
above. See [An unreadable storage location is not an empty
one](concepts/failure-behavior.md#an-unreadable-storage-location-is-not-an-empty-one).

## Backup timestamps are in the wrong timezone

Timestamps use the timezone that ezbak is configured with. When none is set,
ezbak uses the system timezone. The container image sets that to `Etc/UTC`, so an
unconfigured container stamps timestamps in UTC.

- In a container, set `TZ` to your IANA timezone, for example
  `TZ=America/New_York`.
- To set the ezbak timezone directly and override the system one, set `EZBAK_TZ`.

See [Backup names](concepts/backup-names.md).

## A scheduled backup failed but the container is still running

This is by design. A scheduled run logs its error and keeps the container up, so
the next run retries. It does not crash the container.

!!! bug "Scheduled failures need a monitor to be noticed"

    A failed scheduled run stays visible in the logs and pings the failure
    endpoint, but nothing crashes to draw your attention. Set
    `EZBAK_HEALTHCHECK_URL` so your monitor alerts you when a scheduled run fails
    or stops happening. See [Monitoring](orchestration/monitoring.md).

## The container exits immediately with no backup

Two configuration gaps cause an immediate exit:

- No action. Set `EZBAK_ACTION` to `backup` or `restore`. Without it, the
  container logs an error and exits non-zero.
- No storage location. Set `EZBAK_STORAGE_PATHS`, `EZBAK_AWS_S3_BUCKET_NAME`, or
  both. A configuration with neither fails validation.

A bad configuration logs a clear message and exits non-zero. It does not print a
traceback.

## Running the container locally loads real credentials

The container reads `.env` and `.env.secrets` from its working directory. On a
development machine, that can load real S3 credentials into a test run. Keep
those files out of any directory you mount into a test container. See
[Environment variables](reference/environment-variables.md).

## An expected file is missing from a backup

ezbak always skips a set of noise files, and your regular expressions can exclude
more.

- The always-excluded names are `.DS_Store`, `@eaDir`, `.Trashes`, `__pycache__`,
  `Thumbs.db`, and `IconCache.db`.
- If you set `include_regex`, ezbak backs up only the files that match it.
- ezbak skips the files that match `exclude_regex`.

See [Including and excluding files](concepts/filtering.md).
