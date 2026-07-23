"""The CLI for EZBak."""

from __future__ import annotations

# Runtime import (not TYPE_CHECKING-only): cappa resolves annotations via get_type_hints
# at collection time, which needs Path in the module namespace.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, Any

import cappa
from nclutils import pp
from pydantic import ValidationError

from ezbak.constants import DEFAULT_COMPRESSION_LEVEL, CLILogLevel, LogLevel
from ezbak.env import EnvConfig
from ezbak.logging import log_validation_errors

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

    s3_bucket_prefix: Annotated[
        str,
        cappa.Arg(
            long="s3-bucket-prefix",
            help="Key prefix within the S3 bucket.",
            propagate=True,
            group=(3, "Optional"),
        ),
    ] = None

    s3_region: Annotated[
        str,
        cappa.Arg(
            long="s3-region",
            help="AWS region. Defaults to boto3's standard resolution (AWS_REGION/AWS_DEFAULT_REGION).",
            propagate=True,
            group=(3, "Optional"),
        ),
    ] = None

    s3_endpoint_url: Annotated[
        str,
        cappa.Arg(
            long="s3-endpoint-url",
            help="Custom S3 endpoint for S3-compatible storage such as MinIO.",
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

    use_checksums: Annotated[
        bool,
        cappa.Arg(
            long="--use-checksums/--no-use-checksums",
            help="Write a .sha256 sidecar for each backup and verify it on restore.",
            group=(3, "Optional"),
            show_default=True,
        ),
    ] = True


@cappa.command(name="restore", invoke="ezbak.cli_commands.restore.main")
class RestoreCommand:
    """Restore a backup."""

    restore_path: Annotated[
        Path,
        cappa.Arg(
            long="restore-path",
            short="d",
            required=True,
            help="The directory to restore into.",
            group=(1, "Required"),
        ),
    ]

    clean_before_restore: Annotated[
        bool,
        cappa.Arg(
            long="clean-before-restore",
            help="Empty the restore path before restoring.",
            group=(3, "Optional"),
        ),
    ] = False

    skip_if_no_backup: Annotated[
        bool,
        cappa.Arg(
            long="skip-if-no-backup",
            help="Exit cleanly instead of failing when no backup exists to restore.",
            group=(3, "Optional"),
        ),
    ] = False

    skip_if_populated: Annotated[
        bool,
        cappa.Arg(
            long="skip-if-populated",
            help="Skip the restore when the target already contains data.",
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

    restore_date: Annotated[
        str,
        cappa.Arg(
            long="restore-date",
            short="t",
            help="Restore the newest backup at or before this time. Formats: YYYY, YYYYMM, YYYYMMDD, YYYYMMDDTHH, YYYYMMDDTHHMM, YYYYMMDDTHHMMSS.",
            group=(3, "Optional"),
        ),
    ] = None

    use_checksums: Annotated[
        bool,
        cappa.Arg(
            long="--use-checksums/--no-use-checksums",
            help="Verify the archive against its .sha256 sidecar on restore.",
            group=(3, "Optional"),
            show_default=True,
        ),
    ] = True


@cappa.command(name="prune", invoke="ezbak.cli_commands.prune.main")
class PruneCommand:
    """Prune backups."""

    keep_last: Annotated[
        int,
        cappa.Arg(
            long="keep-last",
            help="Number of most recent backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    keep_yearly: Annotated[
        int,
        cappa.Arg(
            long="keep-yearly",
            short="Y",
            help="Number of yearly backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    keep_monthly: Annotated[
        int,
        cappa.Arg(
            long="keep-monthly",
            short="M",
            help="Number of monthly backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    keep_weekly: Annotated[
        int,
        cappa.Arg(
            long="keep-weekly",
            short="W",
            help="Number of weekly backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    keep_daily: Annotated[
        int,
        cappa.Arg(
            long="keep-daily",
            short="D",
            help="Number of daily backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    keep_hourly: Annotated[
        int,
        cappa.Arg(
            long="keep-hourly",
            short="H",
            help="Number of hourly backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    keep_minutely: Annotated[
        int,
        cappa.Arg(
            long="keep-minutely",
            short="S",
            help="Number of minutely backups to keep.",
            group=(2, "Retention"),
        ),
    ] = None

    dry_run: Annotated[
        bool,
        cappa.Arg(
            long="dry-run",
            help="Preview the backups that would be deleted without removing anything.",
            group=(3, "Optional"),
            show_default=False,
        ),
    ] = False

    force: Annotated[
        bool,
        cappa.Arg(
            long="force",
            help="Skip the confirmation prompt and prune immediately.",
            group=(3, "Optional"),
            show_default=False,
        ),
    ] = False


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

    Raises:
        cappa.Exit: If the assembled config is invalid (e.g. no storage location),
            so the user sees logged messages and a clean non-zero exit instead of a
            raw pydantic traceback.
    """
    cmd = cli.command

    # Values the CLI has no flags for (AWS credentials, tz) must still come from the
    # environment. Build the config as EnvConfig with the CLI-derived values passed as
    # explicit overrides (init kwargs are highest priority in pydantic-settings); EnvConfig
    # fills ONLY the omitted fields (tz, aws_access_key, aws_secret_key) from EZBAK_-prefixed
    # env vars. Do NOT construct a bare EnvConfig() to read those first: bare construction
    # runs validate_settings, which requires name + storage (both flag-supplied, not in
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
        "aws_s3_bucket_prefix": cli.s3_bucket_prefix,
        "aws_region": cli.s3_region,
        "aws_s3_endpoint_url": cli.s3_endpoint_url,
    }

    if isinstance(cmd, CreateCommand):
        extra: dict[str, Any] = {
            "source_paths": cmd.sources,
            "strip_source_paths": cmd.strip_source_paths,
            "include_regex": cmd.include_regex,
            "exclude_regex": cmd.exclude_regex,
            "compression_level": cmd.compression_level,
            "use_checksums": cmd.use_checksums,
        }
    elif isinstance(cmd, RestoreCommand):
        extra = {
            "restore_path": cmd.restore_path,
            "restore_date": cmd.restore_date,
            "clean_before_restore": cmd.clean_before_restore,
            "skip_if_no_backup": cmd.skip_if_no_backup,
            "skip_restore_if_populated": cmd.skip_if_populated,
            "chown_uid": cmd.uid,
            "chown_gid": cmd.gid,
            "use_checksums": cmd.use_checksums,
        }
    elif isinstance(cmd, PruneCommand):
        extra = {
            "keep_last": cmd.keep_last,
            "keep_yearly": cmd.keep_yearly,
            "keep_monthly": cmd.keep_monthly,
            "keep_weekly": cmd.keep_weekly,
            "keep_daily": cmd.keep_daily,
            "keep_hourly": cmd.keep_hourly,
            "keep_minutely": cmd.keep_minutely,
        }
    else:  # ListCommand and any other read-only command
        extra = {}

    try:
        return EnvConfig(**common, **extra, _env_file=None)  # type: ignore[call-arg]
    except ValidationError as e:
        log_validation_errors(e)
        raise cappa.Exit(code=1) from e


def main() -> None:  # pragma: no cover
    """Main function."""  # noqa: DOC501
    try:
        cappa.invoke(obj=EZBakCLI, completion=False)
    except KeyboardInterrupt as e:
        pp.info("Exiting...")
        raise cappa.Exit(code=1) from e


if __name__ == "__main__":  # pragma: no cover
    main()
