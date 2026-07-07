"""EZBak package for automated backup operations with retention policies and compression."""

from ezbak.config import BackupConfig
from ezbak.core import EZBak, ezbak

__all__ = ["BackupConfig", "EZBak", "ezbak"]
