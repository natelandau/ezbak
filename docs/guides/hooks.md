---
icon: lucide/webhook
---

# Container lifecycle hooks

A hook runs a shell command before or after the container backs up or restores.
Use one to quiesce a data source first, or to delete a temporary file afterward.
This is a container feature. The CLI and the Python library run a backup or
restore inline in your own code, so you wrap them with your own logic instead.

## The four hook points

Set any of these to a shell command. An unset hook is a no-op.

| Variable | Fires |
| --- | --- |
| `EZBAK_PRE_BACKUP_HOOK` | Before the container creates a backup. |
| `EZBAK_POST_BACKUP_HOOK` | After the container creates a backup and prunes retention. |
| `EZBAK_PRE_RESTORE_HOOK` | Before the container restores a backup. |
| `EZBAK_POST_RESTORE_HOOK` | After the container restores a backup. |

Hooks fire on every run the container makes: a one-shot run, each tick of
`EZBAK_CRON`, and the final backup on shutdown when `EZBAK_BACKUP_ON_SHUTDOWN` is
set. For those run modes, see [Running in Docker](docker.md).

## Tools your hooks need

The container image is lean. It ships `sh`, `python3`, `curl`, `tar`, `sqlite3`,
and the ezbak runtime. It does not ship the database and sync tools that most
hooks use, such as `rsync` or `pg_dump`. A hook that calls a tool the image lacks
fails with a `not found` error, and a failed pre-hook aborts the backup.

Bake the tools you need into your own image. The runtime is Debian-based and runs
as root, so install them with `apt-get` in a Dockerfile that starts from ezbak:

```dockerfile
FROM ghcr.io/natelandau/ezbak:latest
RUN apt-get update \
    && apt-get install -y --no-install-recommends rsync postgresql-client \
    && rm -rf /var/lib/apt/lists/*
```

Build that image and run it in place of the stock one. The tools are then present
on every start, pinned to the versions you built, and available with no network
access at runtime. The Postgres and Airflow images document the same pattern for
extending a base image, and it keeps your deployment reproducible.

!!! tip "Quick experiment without a rebuild"

    Before you commit to a Dockerfile, you can install a tool in the hook itself:
    `EZBAK_PRE_BACKUP_HOOK='apt-get update && apt-get install -y rsync && rsync ...'`.
    This installs the tool again on every run, needs network access each time,
    and runs as root. Treat it as a stopgap, and move the install into your image
    once the hook works.

## SQLite needs no hook

ezbak snapshots live SQLite databases itself. List each one in
`EZBAK_SQLITE_PATHS`. ezbak then copies it through the online-backup API of
SQLite. It runs an integrity check on the copy, and archives it in the place of
the live file. Do not write a hook for this. See
[SQLite databases](../concepts/sqlite.md).

## Worked example: dumping a Postgres database

Postgres runs as a separate service, so ezbak has nothing on disk it can copy
consistently. Dump the database to a file before the backup, and delete the dump
afterward:

```bash
EZBAK_PRE_BACKUP_HOOK='pg_dump -Fc -f /data/app.dump app'
EZBAK_POST_BACKUP_HOOK='rm -f /data/app.dump'
```

The pre-backup hook writes `/data/app.dump`, a point-in-time export that is safe
to archive while the database keeps serving traffic. Point `EZBAK_SOURCE_PATHS`
at the directory that holds the dump. The post-backup hook deletes the dump once
the backup exists, so a second run starts from a clean directory instead of
archiving a stale leftover.

The stock image does not ship `pg_dump`. For the Dockerfile that bakes it in, see
[Tools your hooks need](#tools-your-hooks-need). Pass a password through
`PGPASSWORD` in the environment instead of writing it into the hook command,
which ezbak logs verbatim. See [How a hook runs](#how-a-hook-runs).

## How a hook runs

Each hook is a single value, not a script file, but its command can point at one:

```bash
EZBAK_PRE_BACKUP_HOOK=/hooks/pre.sh
```

ezbak runs the command through `/bin/sh -c "$COMMAND"`, so a path such as
`/hooks/pre.sh` is a valid command. Keep the logic short and inline, or put it in
a script that you mount or bake into the image and reference by path. Either way
the shell parses the command, so pipes, `&&`, and quoting all work.

The hook inherits the environment of the container, including every `EZBAK_`
variable. A script can therefore read `EZBAK_NAME` or `EZBAK_SOURCE_PATHS`
without you repeating them.

!!! tip "Test a hook in the running container"

    Run `docker exec` into the container and run the command by hand. Read its
    exit code and its output before you set `EZBAK_PRE_BACKUP_HOOK` or
    `EZBAK_POST_BACKUP_HOOK`.

!!! warning "Do not put secrets in the command"

    Pass secrets through environment variables that the command reads, so the
    value never appears in the logged command line. ezbak logs the hook command
    and its captured output verbatim. A secret written directly into
    `EZBAK_PRE_BACKUP_HOOK`, or into any other hook variable, appears in the
    container logs.

## Failure semantics

A pre-hook and a post-hook fail differently. A pre-hook runs before ezbak writes
anything, and a post-hook runs after.

| Hook | On failure |
| --- | --- |
| Pre-hook | Aborts the operation. ezbak never starts a backup or restore whose source or target the hook cannot prepare. |
| Post-hook | Fails the run, but keeps the backup or restore. ezbak already wrote the archive, or the restore already landed, before the post-hook ran. |

Either failure fails the run the same way. A one-shot run exits non-zero. A
scheduled run logs the error, keeps the container alive for the next tick, and
pings the `/fail` endpoint of the healthcheck. A non-zero hook logs its exit code
and its captured output. A hook that the timeout killed logs a
timed-out-and-killed message with whatever output it produced. See
[Failure behavior](../concepts/failure-behavior.md) for how each interface
signals a failure, and [Monitoring](../orchestration/monitoring.md) for the
healthcheck ping.

!!! warning "pre-restore fires even when ezbak restores nothing"

    `EZBAK_PRE_RESTORE_HOOK` runs before ezbak reads whether a matching backup
    exists, and before it reads whether the target already holds data. The
    pre-restore hook therefore still runs on a fresh deployment with
    `EZBAK_SKIP_IF_NO_BACKUP` set, and on a populated target with
    `EZBAK_SKIP_RESTORE_IF_POPULATED` set. The post-restore hook does not run,
    because no restore happened. Write a pre-restore hook that tolerates a run
    with nothing to restore. See [Fresh
    deploys](../orchestration/fresh-deploys.md) and [Restore
    backups](restore.md).

## Debugging a hook

When a hook misbehaves, raise `EZBAK_LOG_LEVEL` and run it again. Hook logging is
tiered, so each level adds detail:

| `EZBAK_LOG_LEVEL` | What you see |
| --- | --- |
| `INFO` (default) | ezbak announces each configured hook at boot, with its timeout, so you can make sure that the container picked it up. Every run logs the command as it starts. Any failure logs the exit code, timeout, or spawn error, along with the captured output of the hook. |
| `DEBUG` | Adds a success line per hook, and the captured stdout and stderr of the hooks that succeed. Use it to inspect a hook that exits `0` but does the wrong thing. |
| `TRACE` | Adds the resolved shell invocation and the effective timeout. |

A hook configured for an action the container is not running never fires, for
example an `EZBAK_PRE_RESTORE_HOOK` on a container whose `EZBAK_ACTION` is
`backup`. ezbak warns about that at boot. It is the usual cause of a hook that
looks configured but never runs.

## Timeout

`EZBAK_HOOK_TIMEOUT` caps how long a hook can run, in seconds. The default is
`300`. Set it to `0` to let a hook run to completion with no limit.

```bash
EZBAK_HOOK_TIMEOUT=60
```

ezbak kills a hook that exceeds the timeout and treats it as a failure, with the
same pre-hook or post-hook behavior described above.

!!! warning "A timeout kills the shell, not its children"

    If you rely on the timeout to bound total run time, keep hook commands
    foreground-only, or make them clean up after themselves. ezbak runs a hook as
    `/bin/sh -c "$COMMAND"` and kills that `sh` process on timeout. When the
    command started its own background processes, the exit of `sh` does not
    force-kill them. `tini`, the init process of the container, still reaps them
    once they finish, but it sends them no kill signal.

On the final backup at shutdown, the pre-hook and post-hook run synchronously
before the container stops. A long timeout, or a disabled one (`0`), therefore
extends the shutdown by the runtime of the hook. The kill grace period of the
orchestrator is the limit.
