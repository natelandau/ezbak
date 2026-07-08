"""The CLI for EZBak."""

from __future__ import annotations

# Runtime import (not TYPE_CHECKING-only): cappa resolves annotations via get_type_hints
# at collection time, which needs Path in the module namespace.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, Any

import cappa
from nclutils import pp

from ezbak.constants import DEFAULT_COMPRESSION_LEVEL, CLILogLevel, LogLevel
from ezbak.env import EnvConfig

if TYPE_CHECKING:
    from ezbak.config import BackupConfig


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
            help="Local storage path(s) for backups. Optional when --s3-bucket is set. Repeat --storage for multiple paths.",
            propagate=True,
            group=(3, "Optional"),
        ),
    ] = None

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

    s3_bucket: Annotated[
        str,
        cappa.Arg(
            long="s3-bucket",
            help="S3 bucket name. Credentials come from EZBAK_AWS_ACCESS_KEY / EZBAK_AWS_SECRET_KEY.",
            propagate=True,
            group=(3, "Optional"),
        ),
    ] = None

    s3_bucket_path: Annotated[
        str,
        cappa.Arg(
            long="s3-bucket-path",
            help="Prefix within the S3 bucket.",
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


def build_config(cli: EZBakCLI) -> BackupConfig:
    """Assemble a ``BackupConfig`` from parsed CLI arguments.

    Map the shared arguments plus the active subcommand's fields onto the single config schema. Read credentials from the environment so secrets never pass through argv.

    Args:
        cli (EZBakCLI): The parsed top-level CLI object.

    Returns:
        BackupConfig: The configuration to hand to ``EZBak``.
    """
    cmd = cli.command

    # Values the CLI has no flags for (AWS credentials, tz) must still come from the
    # environment. Build the config as EnvConfig with the CLI-derived values passed as
    # explicit overrides (init kwargs are highest priority in pydantic-settings); EnvConfig
    # fills ONLY the omitted fields (tz, aws_access_key, aws_secret_key) from EZBAK_-prefixed
    # env vars. Do NOT construct a bare EnvConfig() to read those first: bare construction
    # runs validate_settings, which requires name + a destination (both flag-supplied, not in
    # env) and would raise. _env_file=None keeps the CLI from silently absorbing a project
    # .env file (matches the prior factory behavior). EnvConfig is a BackupConfig subclass,
    # so returning it satisfies the -> BackupConfig contract.
    # dict[str, Any]: values fan out to differently-typed EnvConfig kwargs below via **splat,
    # which mypy can only accept if the dict's value type is Any.
    common: dict[str, Any] = {
        "name": cli.name,
        "storage_paths": cli.storage_paths,
        "log_level": LogLevel(cli.verbosity.name),
        "log_file": str(cli.log_file) if cli.log_file else None,
        "log_prefix": cli.log_prefix,
        "aws_s3_bucket_name": cli.s3_bucket,
        "aws_s3_bucket_path": cli.s3_bucket_path,
    }

    if isinstance(cmd, CreateCommand):
        extra: dict[str, Any] = {
            "source_paths": cmd.sources,
            "strip_source_paths": cmd.strip_source_paths,
            "include_regex": cmd.include_regex,
            "exclude_regex": cmd.exclude_regex,
            "compression_level": cmd.compression_level,
        }
    elif isinstance(cmd, RestoreCommand):
        extra = {
            "restore_path": cmd.destination,
            "clean_before_restore": cmd.clean,
            "chown_uid": cmd.uid,
            "chown_gid": cmd.gid,
        }
    elif isinstance(cmd, PruneCommand):
        extra = {
            "max_backups": cmd.max_backups,
            "retention_yearly": cmd.yearly,
            "retention_monthly": cmd.monthly,
            "retention_weekly": cmd.weekly,
            "retention_daily": cmd.daily,
            "retention_hourly": cmd.hourly,
            "retention_minutely": cmd.minutely,
        }
    else:  # ListCommand and any other read-only command
        extra = {}

    return EnvConfig(**common, **extra, _env_file=None)  # type: ignore[call-arg]


def main() -> None:  # pragma: no cover
    """Main function."""  # noqa: DOC501
    try:
        cappa.invoke(obj=EZBakCLI, completion=False)
    except KeyboardInterrupt as e:
        pp.info("Exiting...")
        raise cappa.Exit(code=1) from e


if __name__ == "__main__":  # pragma: no cover
    main()
