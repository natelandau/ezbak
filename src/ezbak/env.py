"""Environment-variable adapter for ezbak configuration.

Load ezbak options from ``EZBAK_``-prefixed environment variables and ``.env`` files, producing a ``BackupConfig``. Used by the CLI and container adapters; library callers use ``BackupConfig`` directly and never trigger env loading.
"""

from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ezbak.config import BackupConfig, make_enum_coercer
from ezbak.constants import ENVAR_PREFIX, Action

coerce_action = make_enum_coercer(Action, error_label="action")


class EnvConfig(BackupConfig, BaseSettings):
    """A ``BackupConfig`` populated from the environment and ``.env`` files.

    Also carries container-runtime settings that a library caller never sets
    (``entrypoint_action``, ``healthcheck_url``), keeping them off the library ``BackupConfig``.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENVAR_PREFIX,
        extra="ignore",
        case_sensitive=False,
        env_file=[".env", ".env.secrets"],
        env_file_encoding="utf-8",
    )

    entrypoint_action: Annotated[Action | None, BeforeValidator(coerce_action)] = Field(
        default=None, alias="ezbak_action"
    )
    healthcheck_url: str | None = None
