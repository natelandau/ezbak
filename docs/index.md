# ezbak

ezbak is a backup manager. It creates, prunes, and restores compressed archives of
files and directories on the local filesystem, in AWS S3, or both, with regex file
filtering, count-based and time-based retention, and point-in-time restore. It was
written primarily to move shared state between jobs and hosts in an orchestrated
environment like Nomad or Kubernetes. In that setting the Docker container is the
main way to run it; a Python package and a command-line tool run the same backups
from your own code or a shell.

ezbak is a small, focused backup manager. It does not aim to replace restic, borg,
or a full backup system.

## What it does

- Creates tar-gzipped (`.tgz`) backups of files and directories.
- Stores backups on the local filesystem, in AWS S3, or both at once.
- Filters files with include and exclude regex patterns.
- Prunes old backups with count-based or time-based retention.
- Restores the latest backup, or the newest backup at or before a point in time.
- Runs scheduled backups in a container with a cron expression.
- Pings a healthcheck monitor so a silent scheduled failure gets noticed.

## Start here

<div class="grid cards" markdown>

-   :material-clock-fast:{ .lg .middle } __Get a backup in five minutes__

    ---

    Install ezbak and make your first backup with the container, CLI, or Python.

    [:octicons-arrow-right-24: Quickstart](getting-started/quickstart.md)

-   :material-cog-transfer:{ .lg .middle } __The orchestration pattern__

    ---

    The workflow ezbak is built for: backups that follow a job across hosts.

    [:octicons-arrow-right-24: Orchestration](orchestration/index.md)

-   :material-tune:{ .lg .middle } __Learn the concepts__

    ---

    Storage locations, retention, filtering, and how failures surface.

    [:octicons-arrow-right-24: Concepts](concepts/storage-locations.md)

-   :material-book-open-variant:{ .lg .middle } __Reference__

    ---

    Every option across the library, CLI, and environment.

    [:octicons-arrow-right-24: Configuration reference](reference/configuration.md)

</div>

## The interface to reach for

ezbak has three interfaces that share one configuration. Which one you use depends
on the job.

| Interface | Use it for |
| --- | --- |
| [Docker container](guides/docker.md) | The primary interface. Orchestrated deployments, scheduled backups. |
| [Command line](guides/cli.md) | Scripting, local testing, one-off backups from a shell. |
| [Python library](guides/python.md) | Driving ezbak from your own code. |

The container is the design center. If you are backing up state for a job under an
orchestrator, start with [the orchestration pattern](orchestration/index.md).
