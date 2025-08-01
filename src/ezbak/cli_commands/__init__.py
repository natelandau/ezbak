"""The CLI for EZBak."""

from pathlib import Path

from ezbak.app import EZBakApp
from ezbak.cli import EZBakCLI
from ezbak.constants import DEFAULT_COMPRESSION_LEVEL, LogLevel, StorageType
from ezbak.models import Settings


def get_app_for_cli(ezbak_cli: EZBakCLI) -> EZBakApp:
    """Get the EZBak app.

    Args:
        ezbak_cli (EZBakCLI): The EZBak CLI.

    Returns:
        EZBakApp: The EZBak app.
    """
    return EZBakApp(
        Settings(  # type: ignore[call-arg]
            name=ezbak_cli.name,
            storage_type=StorageType.LOCAL,
            source_paths=getattr(ezbak_cli.command, "sources", [Path.cwd()]),
            storage_paths=ezbak_cli.storage_paths,
            strip_source_paths=getattr(ezbak_cli.command, "strip_source_paths", False),
            include_regex=getattr(ezbak_cli.command, "include_regex", None),
            exclude_regex=getattr(ezbak_cli.command, "exclude_regex", None),
            compression_level=getattr(
                ezbak_cli.command, "compression_level", DEFAULT_COMPRESSION_LEVEL
            ),
            label_time_units=not getattr(ezbak_cli.command, "no_label", False),
            max_backups=getattr(ezbak_cli.command, "max_backups", None),
            log_level=LogLevel(ezbak_cli.verbosity.name),
            log_file=str(ezbak_cli.log_file) if ezbak_cli.log_file else None,
            log_prefix=ezbak_cli.log_prefix,
            retention_yearly=getattr(ezbak_cli.command, "yearly", None),
            retention_monthly=getattr(ezbak_cli.command, "monthly", None),
            retention_weekly=getattr(ezbak_cli.command, "weekly", None),
            retention_daily=getattr(ezbak_cli.command, "daily", None),
            retention_hourly=getattr(ezbak_cli.command, "hourly", None),
            retention_minutely=getattr(ezbak_cli.command, "minutely", None),
            clean_before_restore=getattr(ezbak_cli.command, "clean", False),
            restore_path=getattr(ezbak_cli.command, "destination", None),
            chown_uid=getattr(ezbak_cli.command, "uid", None),
            chown_gid=getattr(ezbak_cli.command, "gid", None),
            _env_file=None,
        )
    )
