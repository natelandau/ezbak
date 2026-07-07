"""Environment-variable adapter for ezbak configuration.

Load ezbak options from ``EZBAK_``-prefixed environment variables and ``.env`` files, producing a ``BackupConfig``. Used by the CLI and container adapters; library callers use ``BackupConfig`` directly and never trigger env loading.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

from ezbak.config import BackupConfig
from ezbak.constants import ENVAR_PREFIX


class EnvConfig(BackupConfig, BaseSettings):
    """A ``BackupConfig`` populated from the environment and ``.env`` files."""

    model_config = SettingsConfigDict(
        env_prefix=ENVAR_PREFIX,
        extra="ignore",
        case_sensitive=False,
        env_file=[".env", ".env.secrets"],
        env_file_encoding="utf-8",
    )
