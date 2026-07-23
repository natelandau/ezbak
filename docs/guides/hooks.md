---
icon: lucide/webhook
---

# Container lifecycle hooks

Hooks run a shell command before or after the container backs up or restores, so
you can quiesce a data source first or clean up a temporary file afterward. This
is a container feature: the CLI and the Python library run a backup or restore
inline in your own code, so you wrap them with your own logic instead.

## The four hook points

Set any of these to a shell command. An unset hook is a no-op.

| Variable | Fires |
| --- | --- |
| `EZBAK_PRE_BACKUP_HOOK` | Before the container creates a backup. |
| `EZBAK_POST_BACKUP_HOOK` | After the container creates a backup and prunes retention. |
| `EZBAK_PRE_RESTORE_HOOK` | Before the container restores a backup. |
| `EZBAK_POST_RESTORE_HOOK` | After the container restores a backup. |

Hooks fire on every run the container makes: a one-shot run, each tick of
`EZBAK_CRON`, and the final backup taken on shutdown when
`EZBAK_BACKUP_ON_SHUTDOWN` is set. See [Running in Docker](docker.md) for those
run modes.

## Tools your hooks need

The container image is lean. It ships `sh`, `python3`, `curl`, `tar`, and the
ezbak runtime, but not the database and sync tools most hooks reach for, such as
`sqlite3`, `rsync`, or `pg_dump`. A hook that calls a tool the image lacks fails
with a `not found` error, and a failing pre-hook aborts the backup.

Bake the tools you need into your own image. The runtime is Debian-based and
runs as root, so install them with `apt-get` in a Dockerfile that starts from
ezbak:

```dockerfile
FROM ghcr.io/natelandau/ezbak:latest
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 rsync postgresql-client \
    && rm -rf /var/lib/apt/lists/*
```

Build that image and run it in place of the stock one. The tools are then
present on every start, pinned to the versions you built, and available with no
network access at runtime. This is the same pattern the Postgres and Airflow
images document for extending a base image, and it keeps your deployment
reproducible.

!!! tip "Quick experiment without rebuilding"

    To try a tool before committing to a Dockerfile, install it in the hook
    itself: `EZBAK_PRE_BACKUP_HOOK='apt-get update && apt-get install -y sqlite3 && sqlite3 ...'`.
    This re-installs on every run, needs network access each time, and runs as
    root, so treat it as a stopgap and move the install into your image once the
    hook works.

## Worked example: quiescing a SQLite database

A running SQLite database can be mid-write when ezbak archives its file, so the
backup can capture a torn page. Run SQLite's own `.backup` command first to write
a consistent snapshot, then archive that snapshot instead of the live file:

```bash
EZBAK_PRE_BACKUP_HOOK='sqlite3 /data/app.db ".backup /data/app.db.bak"'
EZBAK_POST_BACKUP_HOOK='rm -f /data/app.db.bak'
```

The pre-backup hook writes `/data/app.db.bak`, a point-in-time copy safe to
archive even while the application keeps writing to `/data/app.db`. Point
`EZBAK_SOURCE_PATHS` at the directory containing the `.bak` file. The
post-backup hook removes the copy once the backup exists, so a second run
starts from a clean directory instead of archiving a stale leftover.

The stock image does not ship `sqlite3`, so add it first. See [Tools your hooks
need](#tools-your-hooks-need) for the Dockerfile that bakes it in.

## How a hook runs

Each hook is a single value, not a script file, but its command can point at
one:

```bash
EZBAK_PRE_BACKUP_HOOK=/hooks/pre.sh
```

ezbak runs the command through `/bin/sh -c "$COMMAND"`, so a path like
`/hooks/pre.sh` is a valid command. Keep the logic short and inline, or put it
in a script that you mount or bake into the image and reference by path. Either
way the shell parses the command, so pipes, `&&`, and quoting all work.

The hook inherits the container's environment, including every `EZBAK_`
variable, so a script can read `EZBAK_NAME` or `EZBAK_SOURCE_PATHS` without
you repeating them.

!!! tip "Test a hook in the running container"

    `docker exec` into the container and run the command by hand to check its
    exit code and output before wiring it into `EZBAK_PRE_BACKUP_HOOK` or
    `EZBAK_POST_BACKUP_HOOK`.

!!! warning "Don't put secrets in the command"

    ezbak logs the hook command and its captured output verbatim, so a secret
    written directly into `EZBAK_PRE_BACKUP_HOOK` or any other hook variable
    ends up in the container logs. Pass secrets through environment variables
    the command reads instead, so the value never appears in the logged
    command line.

## Failure semantics

A pre-hook and a post-hook fail differently, because a pre-hook runs before
anything is written and a post-hook runs after.

- **A failing pre-hook aborts the operation.** ezbak never starts a backup or
  restore whose source or target the hook could not prepare.
- **A failing post-hook fails the run but keeps the backup or restore.** The
  archive was already written, or the restore already landed, before the
  post-hook ran, so ezbak keeps that result and reports the run as failed.

Either failure fails the run the same way: a one-shot run exits non-zero, and a
scheduled run logs the error, keeps the container alive for the next tick, and
pings the healthcheck's `/fail` endpoint. A non-zero hook logs its exit code and
captured output; a hook killed by the timeout logs a timed-out-and-killed
message with whatever output it produced. See [Failure
behavior](../concepts/failure-behavior.md) for how each interface signals a
failure and [Monitoring](../orchestration/monitoring.md) for the healthcheck
ping.

!!! warning "pre-restore fires even when nothing gets restored"

    `EZBAK_PRE_RESTORE_HOOK` runs before ezbak checks whether a matching backup
    exists or whether the target already has data. On a fresh deployment with
    `EZBAK_RESTORE_IF_EXISTS` set, or on a populated target with
    `EZBAK_SKIP_RESTORE_IF_POPULATED` set, the pre-restore hook still runs, but
    the post-restore hook does not, because no restore happened. Write a
    pre-restore hook that tolerates running with nothing to restore. See [Fresh
    deploys](../orchestration/fresh-deploys.md) and [Restore
    backups](restore.md).

## Debugging a hook

When a hook misbehaves, raise `EZBAK_LOG_LEVEL` and re-run. Hook logging is
tiered so each level adds detail:

| `EZBAK_LOG_LEVEL` | What you see |
| --- | --- |
| `INFO` (default) | Each configured hook is announced at boot with its timeout, so you can confirm the container picked it up. Every run logs the command as it starts, and any failure logs the exit code, timeout, or spawn error along with the hook's captured output. |
| `DEBUG` | Adds a success line per hook and the captured stdout/stderr of hooks that succeed, so you can inspect a hook that exits `0` but does the wrong thing. |
| `TRACE` | Adds the resolved shell invocation and effective timeout. |

A hook configured for the action the container is not running (a
`EZBAK_PRE_RESTORE_HOOK` on a container whose `EZBAK_ACTION` is `backup`, for
example) never fires. ezbak warns about that at boot, since it is the usual cause
of a hook that seems configured but never runs.

## Timeout

`EZBAK_HOOK_TIMEOUT` caps how long a hook can run, in seconds. The default is
`300`. Set it to `0` to let a hook run to completion with no limit.

```bash
EZBAK_HOOK_TIMEOUT=60
```

A hook that exceeds the timeout is killed and treated as a failure, with the
same pre- or post-hook semantics described above.

!!! warning "A timeout kills the shell, not its children"

    ezbak runs a hook as `/bin/sh -c "$COMMAND"` and kills that `sh` process on
    timeout. If the command started its own background processes, `sh` exiting
    does not force-kill them. The container's init process, `tini`, still reaps
    them once they finish, but it does not send them a kill signal. Keep hook
    commands foreground-only, or have them clean up after themselves, if you
    rely on the timeout to bound total run time.

On the final backup at shutdown, the pre- and post-hooks run synchronously
before the container stops, so a long-running or disabled (`0`) hook timeout
extends how long shutdown takes, up to the orchestrator's kill grace period.
