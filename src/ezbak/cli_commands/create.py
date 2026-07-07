"""The create command for the EZBak CLI."""

from __future__ import annotations

from ezbak.cli import EZBakCLI, build_config
from ezbak.core import EZBak


def main(cmd: EZBakCLI) -> None:
    """The main function for the create command."""
    app = EZBak(build_config(cmd))
    app.create_backup()
