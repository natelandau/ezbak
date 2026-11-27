"""Tests for BackupConfig and EnvConfig."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ezbak.config import BackupConfig, coerce_path_list
from ezbak.env import EnvConfig

# Builds (source_paths, sqlite_paths) for a config from a scratch directory
SqliteCase = Callable[[Path], tuple[list[Any], Any]]


def test_backupconfig_requires_name():
    """Verify BackupConfig rejects a missing name."""
    # Given no name and a destination
    # When constructing the config
    # Then validation fails
    with pytest.raises(ValidationError):
        BackupConfig(storage_paths=["/tmp"])


def test_backupconfig_does_not_read_env(monkeypatch):
    """Verify BackupConfig ignores EZBAK_ environment variables."""
    # Given an env var that would set the name
    monkeypatch.setenv("EZBAK_NAME", "from-env")

    # When building a plain BackupConfig with an explicit name
    config = BackupConfig(name="explicit", storage_paths=["/tmp"])

    # Then the env var is not consulted
    assert config.name == "explicit"


@pytest.mark.parametrize(
    ("env", "field", "expected"),
    [
        ({}, "name", "from-env"),
        ({"EZBAK_RESTORE_DATE": "20250102"}, "restore_date", "20250102"),
        ({"EZBAK_AWS_REGION": "ap-southeast-2"}, "aws_region", "ap-southeast-2"),
        (
            {"EZBAK_AWS_S3_ENDPOINT_URL": "https://minio.example.com"},
            "aws_s3_endpoint_url",
            "https://minio.example.com",
        ),
        (
            {"EZBAK_SOURCE_PATHS": "/data", "EZBAK_SQLITE_PATHS": "/data/gitea/gitea.db"},
            "sqlite_paths",
            [Path("/data/gitea/gitea.db")],
        ),
        ({"EZBAK_BACKUP_ON_SHUTDOWN": "true"}, "backup_on_shutdown", True),
        ({"EZBAK_CRON_JITTER": "120"}, "cron_jitter", 120),
        (
            {"EZBAK_PRE_BACKUP_HOOK": 'sqlite3 /data/db ".backup /data/db.bak"'},
            "pre_backup_hook",
            'sqlite3 /data/db ".backup /data/db.bak"',
        ),
        ({"EZBAK_HOOK_TIMEOUT": "30"}, "hook_timeout", 30),
    ],
    ids=[
        "name",
        "restore-date",
        "region",
        "endpoint-url",
        "sqlite-paths",
        "backup-on-shutdown",
        "cron-jitter",
        "pre-backup-hook",
        "hook-timeout",
    ],
)
def test_envconfig_reads_env(monkeypatch, env, field, expected):
    """Verify EnvConfig loads each EZBAK_-prefixed environment variable into its field."""
    # Given the required fields plus one option in the environment
    monkeypatch.setenv("EZBAK_NAME", "from-env")
    monkeypatch.setenv("EZBAK_STORAGE_PATHS", "/tmp")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # When building an EnvConfig
    config = EnvConfig()

    # Then the field holds the parsed value
    assert getattr(config, field) == expected


@pytest.mark.parametrize(
    "env_var",
    ["EZBAK_CRON_JITTER", "EZBAK_HOOK_TIMEOUT"],
    ids=["cron-jitter", "hook-timeout"],
)
def test_envconfig_rejects_negative(monkeypatch, env_var):
    """Verify a mistyped negative duration is rejected when the environment is loaded."""
    # Given an otherwise valid environment with one negative duration
    monkeypatch.setenv("EZBAK_NAME", "from-env")
    monkeypatch.setenv("EZBAK_STORAGE_PATHS", "/tmp")
    monkeypatch.setenv(env_var, "-1")

    # When building an EnvConfig
    # Then validation fails
    with pytest.raises(ValidationError):
        EnvConfig()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", []),
        ("/tmp/a,,/tmp/b", [Path("/tmp/a"), Path("/tmp/b")]),
        ("/tmp/a, /tmp/b", [Path("/tmp/a"), Path("/tmp/b")]),
    ],
    ids=["empty", "blank-segment", "padded"],
)
def test_coerce_path_list(value, expected):
    """Verify a comma list yields only its real paths, never a phantom cwd-relative entry."""
    # Given a comma-separated value like an env var supplies
    # When coercing it
    # Then only the real, stripped paths remain
    assert coerce_path_list(value) == expected


def test_retention_policy_derivation_keep_last():
    """Verify keep_last yields an active retention policy."""
    # Given a config with keep_last
    config = BackupConfig(name="x", storage_paths=["/tmp"], keep_last=5)

    # When reading the derived policy
    policy = config.retention_policy

    # Then it is active with the configured keep_last
    assert policy.is_active
    assert policy.keep_last == 5


@pytest.mark.parametrize(
    "field",
    [
        "keep_last",
        "keep_minutely",
        "keep_hourly",
        "keep_daily",
        "keep_weekly",
        "keep_monthly",
        "keep_yearly",
    ],
)
def test_negative_keep_rule_rejected(field):
    """Verify a negative keep rule is rejected instead of pruning the wrong backups."""
    # Given an otherwise valid config with one negative keep rule
    negative_rule: dict[str, Any] = {field: -1}

    # When constructing it
    # Then validation fails rather than accepting the negative count
    with pytest.raises(ValidationError):
        BackupConfig(name="x", storage_paths=["/tmp"], **negative_rule)


@pytest.mark.parametrize("log_level", [None, "loud"])
def test_unusable_log_level_rejected(log_level):
    """Verify a missing or unknown log level fails validation instead of crashing logger setup."""
    # Given an otherwise valid config with an unusable log level
    # When constructing it
    # Then validation fails
    with pytest.raises(ValidationError):
        BackupConfig(name="x", storage_paths=["/tmp"], log_level=log_level)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            lambda _: (["/data"], "/data/a.db,/data/b.db"),
            lambda _: [Path("/data/a.db"), Path("/data/b.db")],
            id="comma-string",
        ),
        pytest.param(
            lambda _: (["/data/gitea.db"], ["/data/gitea.db"]),
            lambda _: [Path("/data/gitea.db")],
            id="file-source-is-db",
        ),
        pytest.param(
            lambda _: (["/data"], ["**/*.db"]),
            lambda _: [Path("**/*.db")],
            id="relative-pattern",
        ),
        # Containment is enforced per match at expansion time, not on the pattern itself
        pytest.param(
            lambda root: ([root / "a", root / "b"], [root / "*" / "*.db"]),
            lambda root: [root / "*" / "*.db"],
            id="pattern-above-sources",
        ),
    ],
)
def test_backupconfig_accepts_sqlite_paths(
    tmp_path, case: SqliteCase, expected: Callable[[Path], list[Path]]
):
    """Verify a usable sqlite path entry is stored as written, with no expansion at construction."""
    # Given existing source directories and a sqlite path entry
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    source_paths, sqlite_paths = case(tmp_path)

    # When constructing the config
    config = BackupConfig(
        name="x", storage_paths=["/tmp"], source_paths=source_paths, sqlite_paths=sqlite_paths
    )

    # Then the entries are stored as written
    assert config.sqlite_paths == expected(tmp_path)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(lambda _: (["/data"], ["/elsewhere/a.db"]), id="outside-sources"),
        pytest.param(
            lambda _: (["/data", "/data/gitea"], ["/data/gitea/gitea.db"]),
            id="overlapping-sources",
        ),
        pytest.param(
            lambda root: ([root / "data"], [root / "data" / "typo" / "*.db"]),
            id="missing-pattern-root",
        ),
        pytest.param(
            lambda root: ([root / "data"], [root / "data" / ".." / "etc" / "app.db"]),
            id="parent-component",
        ),
        pytest.param(
            lambda root: ([root / "data"], [root / "data" / "**.db"]),
            id="partial-double-star",
        ),
    ],
)
def test_backupconfig_rejects_sqlite_paths(tmp_path, case: SqliteCase):
    """Verify a sqlite path entry that cannot produce a restorable snapshot fails at construction."""
    # Given an existing source directory and an unusable sqlite path entry
    (tmp_path / "data").mkdir()
    source_paths, sqlite_paths = case(tmp_path)

    # When constructing the config
    # Then validation fails instead of failing mid-backup
    with pytest.raises(ValidationError):
        BackupConfig(
            name="x", storage_paths=["/tmp"], source_paths=source_paths, sqlite_paths=sqlite_paths
        )
