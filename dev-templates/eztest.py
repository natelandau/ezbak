"""Scratchpad for testing ezbak.

Copy this file into .dev and run `python eztest.py` to test ezbak.
"""  # noqa: INP001

from ezbak import ezbak

backup_manager = ezbak(
    name="test",
    source_paths=[".dev/source/project1", ".dev/source/project2"],
    storage_paths=[".dev/backups"],
)

backup_manager.create_backup()
