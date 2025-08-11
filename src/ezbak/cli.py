"""The CLI for EZBak."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import Annotated

import cappa
from nclutils import pp

from ezbak.constants import DEFAULT_COMPRESSION_LEVEL, CLILogLevel


@cappa.command(name="ezbak")
class EZBakCLI:
    """The EZBak CLI."""

    command: cappa.Subcommands[CreateCommand | RestoreCommand | PruneCommand | ListCommand]

    name: Annotated[
        str,
        cappa.Arg(
            required=True,
            help="Short name for the backup. _Timestamps and labels are automatically inferred._",
            propagate=True,
            long="name",
            short="n",
            group=(1, "Required"),
        ),
    ]
    storage_paths: Annotated[
        list[Path],
        cappa.Arg(
            long="storage",
            help="The storage path(s) where backups are stored. Add multiple storage paths with multiple --storage flags.",
            propagate=True,
            group=(1, "Required"),
        ),
    ]

    verbosity: Annotated[
        CLILogLevel,
        cappa.Arg(
            short=True,
            count=True,
            help="Verbosity level (`-v` or `-vv`)",
            choices=[],
            show_default=False,
            propagate=True,
            group=(3, "Optional"),
        ),
    ] = CLILogLevel.INFO

    log_file: Annotated[
        Path | str,
        cappa.Arg(
            long="log-file",
            required=False,
            help="The log file.",
            propagate=True,
            group=(3, "Optional"),
        ),
    ] = None

    log_prefix: Annotated[
        str,
        cappa.Arg(
            long="log-prefix",
            help="Prefix for log messages.",
            propagate=True,
            group=(3, "Optional"),
        ),
    ] = None


@cappa.command(name="create", invoke="ezbak.cli_commands.create.main")
class CreateCommand:
    """Create a backup."""

    sources: Annotated[
        list[Path | str],
        cappa.Arg(
            long="source",
            required=True,
            help="Source path(s) to backup. Add multiple sources with multiple --source flags.",
            group=(1, "Required"),
        ),
    ]

    include_regex: Annotated[
        str,
        cappa.Arg(
            long="include-regex",
            short="i",
            help="The regex to include in the backup.",
            group=(3, "Optional"),
        ),
    ] = None

    exclude_regex: Annotated[
        str,
        cappa.Arg(
            long="exclude-regex",
            short="e",
            help="The regex to exclude from the backup.",
            group=(3, "Optional"),
        ),
    ] = None

    strip_source_paths: Annotated[
        bool,
        cappa.Arg(
            long="strip-source-paths",
            short="s",
            help="Strip source paths from directory sources. (e.g. /source/foo.txt -> foo.txt)",
            group=(3, "Optional"),
            show_default=False,
        ),
    ] = False

    compression_level: Annotated[
        int,
        cappa.Arg(
            long="compression-level",
            short="c",
            help="The compression level.",
            choices=range(1, 10),
            group=(3, "Optional"),
        ),
    ] = DEFAULT_COMPRESSION_LEVEL

    no_label: Annotated[
        bool,
        cappa.Arg(
            long=["no-label"],
            help="Do not include time labels in the backup filename. (e.g. daily, weekly, etc.)",
            group=(3, "Optional"),
            show_default=False,
        ),
    ] = False


@cappa.command(name="restore", invoke="ezbak.cli_commands.restore.main")
class RestoreCommand:
    """Restore a backup."""

    destination: Annotated[
        Path,
        cappa.Arg(
            long="destination",
            short="d",
            required=True,
            help="The directory to restore to.",
            group=(1, "Required"),
        ),
    ]

    clean: Annotated[
        bool,
        cappa.Arg(
            long="clean",
            help="Clean the destination directory before restoring.",
            group=(3, "Optional"),
        ),
    ] = False

    uid: Annotated[
        int,
        cappa.Arg(
            long="uid",
            short="u",
            help="Post restore chown user.",
            group=(3, "Optional"),
        ),
    ] = None

    gid: Annotated[
        int,
        cappa.Arg(
            long="gid",
            short="g",
            help="Post restore chown group.",
            group=(3, "Optional"),
        ),
    ] = None


@cappa.command(name="prune", invoke="ezbak.cli_commands.prune.main")
class PruneCommand:
    """Prune backups."""

    max_backups: Annotated[
        int,
        cappa.Arg(
            long="max-backups",
            short="x",
            help="The maximum number of backups to prune.",
            group=(2, "Retention"),
        ),
    ] = None

    yearly: Annotated[
        int,
        cappa.Arg(
            long="yearly",
            short="Y",
            help="The number of yearly backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    monthly: Annotated[
        int,
        cappa.Arg(
            long="monthly",
            short="M",
            help="The number of monthly backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    weekly: Annotated[
        int,
        cappa.Arg(
            long="weekly",
            short="W",
            help="The number of weekly backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    daily: Annotated[
        int,
        cappa.Arg(
            long="daily",
            short="D",
            help="The number of daily backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    hourly: Annotated[
        int,
        cappa.Arg(
            long="hourly",
            short="H",
            help="The number of hourly backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    minutely: Annotated[
        int,
        cappa.Arg(
            long="minutely",
            short="S",
            help="The number of minutely backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None


@cappa.command(name="list", invoke="ezbak.cli_commands.list.main")
class ListCommand:
    """List backups."""


def main() -> None:  # pragma: no cover
    """Main function."""  # noqa: DOC501
    try:
        cappa.invoke(obj=EZBakCLI, completion=False)
    except KeyboardInterrupt as e:
        pp.info("Exiting...")
        raise cappa.Exit(code=1) from e


if __name__ == "__main__":  # pragma: no cover
    main()
