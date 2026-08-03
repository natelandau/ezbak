---
icon: lucide/house
---

# ezbak

ezbak is a backup manager. It creates, prunes, and restores compressed archives of
files and directories. It writes them to the local filesystem, to AWS S3, or to
both. It filters files with regular expressions, prunes with count-based and
time-based rules, and restores a backup from a point in time.

ezbak was written to move shared state between jobs and hosts under an
orchestrator such as Nomad or Kubernetes. In that setting the Docker container is
the main way to run it. A Python package and a command-line tool run the same
backups from your own code or from a shell.

ezbak stays small and focused. It is not a replacement for a full backup system
such as [Restic](https://restic.net) or
[Borg](https://borgbackup.readthedocs.io/en/stable/).

## What it does

- Creates tar-gzipped (`.tgz`) backups of files and directories.
- Stores backups on the local filesystem, in AWS S3, or in both at once.
- Filters files with include and exclude regular expressions.
- Prunes old backups with keep rules that combine count-based and time-based
  retention.
- Restores the latest backup, or the newest backup at or before a point in time.
- Runs scheduled backups in a container with a cron expression.
- Pings a healthcheck monitor from the container, so you learn about a silent
  failure.
- Runs shell hooks before and after a container backup or restore. See
  [Container lifecycle hooks](guides/hooks.md).

## Start here

<div class="grid cards" markdown>

- :material-clock-fast:{ .lg .middle } **Get a backup in five minutes**

    ***

    Install ezbak and make your first backup with the container, CLI, or Python.

    [:octicons-arrow-right-24: Quickstart](getting-started/quickstart.md)

- :material-cog-transfer:{ .lg .middle } **The orchestration pattern**

    ***

    The workflow ezbak is built for: backups that follow a job across hosts.

    [:octicons-arrow-right-24: Orchestration](orchestration/index.md)

- :material-tune:{ .lg .middle } **Learn the concepts**

    ***

    Storage locations, retention, filtering, and how failures surface.

    [:octicons-arrow-right-24: Concepts](concepts/storage-locations.md)

- :material-book-open-variant:{ .lg .middle } **Reference**

    ***

    Every option across the library, CLI, and environment.

    [:octicons-arrow-right-24: Configuration reference](reference/configuration.md)

</div>

## Which interface to use

ezbak has three interfaces that share one configuration. The job decides which
one you use.

| Interface                            | Use it for                                                          |
| ------------------------------------ | ------------------------------------------------------------------- |
| [Docker container](guides/docker.md) | The primary interface. Orchestrated deployments, scheduled backups. |
| [Command line](guides/cli.md)        | Scripting, local tests, one-off backups from a shell.               |
| [Python library](guides/python.md)   | Driving ezbak from your own code.                                   |

The container is the design center. If you back up the state of a job under an
orchestrator, start with [the orchestration pattern](orchestration/index.md).
