"""The CLI for EZBak."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import cappa
from nclutils import pp

from ezbak import ezbak
from ezbak.constants import CLILogLevel
from ezbak.controllers import BackupManager  # noqa: TC001


def initialize_ezbak(ezbak_cli: EZBakCLI) -> None:
    """Initialize the EZBak CLI."""
    name = ezbak_cli.name
    log_level = ezbak_cli.verbosity.name
    log_file = str(ezbak_cli.log_file) if ezbak_cli.log_file else None

    sources = getattr(ezbak_cli.command, "sources", [Path.cwd()])
    destinations = getattr(ezbak_cli.command, "destinations", [Path.cwd()])
    include_regex = getattr(ezbak_cli.command, "include_regex", None)
    exclude_regex = getattr(ezbak_cli.command, "exclude_regex", None)
    compression_level = getattr(ezbak_cli.command, "compression_level", None)
    no_label = getattr(ezbak_cli.command, "no_label", None)
    label_time_units = not no_label
    max_backups = getattr(ezbak_cli.command, "max_backups", None)

    time_based_policy = {
        "yearly": getattr(ezbak_cli.command, "yearly", None),
        "monthly": getattr(ezbak_cli.command, "monthly", None),
        "weekly": getattr(ezbak_cli.command, "weekly", None),
        "daily": getattr(ezbak_cli.command, "daily", None),
        "hourly": getattr(ezbak_cli.command, "hourly", None),
        "minutely": getattr(ezbak_cli.command, "minutely", None),
    }

    backup_manager = ezbak(
        name=name,
        sources=sources,
        destinations=destinations,
        include_regex=include_regex,
        exclude_regex=exclude_regex,
        compression_level=compression_level,
        label_time_units=label_time_units,
        max_backups=max_backups,
        log_level=log_level or None,
        log_file=log_file,
        time_based_policy=time_based_policy,
    )
    ezbak_cli.backup_manager = backup_manager


@cappa.command(name="ezback")
class EZBakCLI:
    """The EZBak CLI."""

    command: cappa.Subcommands[CreateCommand | RestoreCommand | PruneCommand | ListCommand]

    name: Annotated[
        str,
        cappa.Arg(
            required=True,
            help="The name of the backup.",
            propagate=True,
            long="name",
            short="n",
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
        ),
    ] = CLILogLevel.INFO

    log_file: Annotated[
        Path | str,
        cappa.Arg(
            long="log-file",
            required=False,
            help="The log file.",
            propagate=True,
        ),
    ] = None
    backup_manager: Annotated[
        BackupManager,
        cappa.Arg(long="backup-manager", hidden=True, propagate=True),
    ] = None


@cappa.command(name="create", invoke="ezbak.cli_commands.create.main")
class CreateCommand:
    """Create a backup."""

    sources: Annotated[
        list[Path | str],
        cappa.Arg(
            long="sources",
            short="s",
            required=True,
            help="The sources to backup.",
        ),
    ]

    destinations: Annotated[
        list[Path | str],
        cappa.Arg(
            long="destinations",
            short="d",
            required=True,
            help="The destinations to backup to.",
        ),
    ]

    include_regex: Annotated[
        str,
        cappa.Arg(
            long="include-regex",
            short="i",
            help="The regex to include in the backup.",
        ),
    ] = None

    exclude_regex: Annotated[
        str,
        cappa.Arg(
            long="exclude-regex",
            short="e",
            help="The regex to exclude from the backup.",
        ),
    ] = None

    compression_level: Annotated[
        int,
        cappa.Arg(
            long="compression-level",
            short="c",
            help="The compression level.",
            choices=range(1, 10),
        ),
    ] = None

    no_label: Annotated[
        bool,
        cappa.Arg(
            long=["no-label"],
            help="Do not include time labels in the backup filename. (e.g. daily, weekly, etc.)",
        ),
    ] = False


@cappa.command(name="restore")
class RestoreCommand:
    """Restore a backup."""

    destination: Annotated[
        Path,
        cappa.Arg(
            long="destination",
            short="d",
            required=True,
            help="The directory to restore to.",
        ),
    ]


@cappa.command(name="prune", invoke="ezbak.cli_commands.prune.main")
class PruneCommand:
    """Prune backups."""

    destinations: Annotated[
        list[Path | str],
        cappa.Arg(
            long="destinations",
            short="d",
            required=True,
            help="The destinations to prune backups from.",
        ),
    ]

    max_backups: Annotated[
        int,
        cappa.Arg(
            long="max-backups",
            short="x",
            help="The maximum number of backups to prune.",
        ),
    ] = None

    yearly: Annotated[
        int,
        cappa.Arg(
            long="yearly",
            short="Y",
            help="The number of yearly backups to keep.",
        ),
    ] = None

    monthly: Annotated[
        int,
        cappa.Arg(
            long="monthly",
            short="M",
            help="The number of monthly backups to keep.",
        ),
    ] = None

    weekly: Annotated[
        int,
        cappa.Arg(
            long="weekly",
            short="W",
            help="The number of weekly backups to keep.",
        ),
    ] = None

    daily: Annotated[
        int,
        cappa.Arg(
            long="daily",
            short="D",
            help="The number of daily backups to keep.",
        ),
    ] = None

    hourly: Annotated[
        int,
        cappa.Arg(
            long="hourly",
            short="H",
            help="The number of hourly backups to keep.",
        ),
    ] = None

    minutely: Annotated[
        int,
        cappa.Arg(
            long="minutely",
            short="S",
            help="The number of minutely backups to keep.",
        ),
    ] = None


@cappa.command(name="list", invoke="ezbak.cli_commands.list.main")
class ListCommand:
    """List backups."""

    destinations: Annotated[
        list[Path | str],
        cappa.Arg(
            long="locations",
            short="l",
            required=True,
            help="The locations to list backups from.",
        ),
    ]


def main() -> None:  # pragma: no cover
    """Main function."""  # noqa: DOC501
    try:
        cappa.invoke(obj=EZBakCLI, deps=[initialize_ezbak])
    except KeyboardInterrupt as e:
        pp.info("Exiting...")
        raise cappa.Exit(code=1) from e


if __name__ == "__main__":  # pragma: no cover
    main()
