"""The create command for the EZBak CLI."""

from __future__ import annotations

from ezbak.cli import EZBakCLI  # noqa: TC001


def main(command: EZBakCLI) -> None:
    """The main function for the create command."""
    command.backup_manager.create_backup()
