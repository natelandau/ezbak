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

    # Opt-in: take a final backup when the cron BACKUP loop receives SIGTERM/SIGINT,
    # shrinking a sidecar's data-loss window to the orchestrator's kill grace period.
    backup_on_shutdown: bool = False

    # Seconds of random delay added to each scheduled run, spreading a fleet of sidecars
    # that share one cron so they do not hit a shared destination at the same instant.
    # ge=0 rejects a mistyped negative EZBAK_CRON_JITTER at load time.
    cron_jitter: int = Field(default=60, ge=0)

    # Operator-configured lifecycle hooks run around container backup/restore runs.
    # Each is a shell command run via `sh -c` (see ezbak.hooks.run_hook); unset is a
    # no-op. Container-only: a library caller wraps its own code around the call.
    pre_backup_hook: str | None = None
    post_backup_hook: str | None = None
    pre_restore_hook: str | None = None
    post_restore_hook: str | None = None
    # Seconds before a hook is killed; 0 runs to completion. ge=0 rejects a mistyped
    # negative EZBAK_HOOK_TIMEOUT at load time.
    hook_timeout: int = Field(default=300, ge=0)
