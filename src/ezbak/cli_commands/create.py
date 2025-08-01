"""The create command for the EZBak CLI."""

from __future__ import annotations

from ezbak.cli import EZBakCLI  # noqa: TC001
from ezbak.cli_commands import get_app_for_cli


def main(cmd: EZBakCLI) -> None:
    """The main function for the create command."""
    app = get_app_for_cli(cmd)
    app.create_backup()
