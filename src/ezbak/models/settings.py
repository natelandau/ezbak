"""Backward-compatible shim. Superseded by ezbak.config.BackupConfig / ezbak.env.EnvConfig."""

from ezbak.config import BackupConfig
from ezbak.env import EnvConfig

# Historical name. Env-loading callers used Settings() with no args, which now maps to EnvConfig.
Settings = EnvConfig

__all__ = ["BackupConfig", "EnvConfig", "Settings"]
