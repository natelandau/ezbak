"""The create command for the EZBak CLI."""

from __future__ import annotations

from ezbak import ezbak


def main() -> None:
    """The main function for the create command."""
    backup_manager = ezbak()
    backup_manager.create_backup()
