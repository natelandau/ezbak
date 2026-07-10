"""Tests for BackupConfig and EnvConfig."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ezbak.config import BackupConfig, coerce_path_list
from ezbak.env import EnvConfig


def test_backupconfig_requires_name():
    """Verify BackupConfig rejects a missing name."""
    # Given no name and a destination
    # When constructing the config
    # Then validation fails
    with pytest.raises(ValidationError):
        BackupConfig(storage_paths=["/tmp"])  # noqa: S108


def test_backupconfig_does_not_read_env(monkeypatch):
    """Verify BackupConfig ignores EZBAK_ environment variables."""
    # Given an env var that would set the name
    monkeypatch.setenv("EZBAK_NAME", "from-env")

    # When building a plain BackupConfig with an explicit name
    config = BackupConfig(name="explicit", storage_paths=["/tmp"])  # noqa: S108

    # Then the env var is not consulted
    assert config.name == "explicit"


def test_envconfig_reads_env(monkeypatch):
    """Verify EnvConfig loads EZBAK_-prefixed environment variables."""
    # Given EZBAK_ env vars
    monkeypatch.setenv("EZBAK_NAME", "from-env")
    monkeypatch.setenv("EZBAK_STORAGE_PATHS", "/tmp")  # noqa: S108

    # When building an EnvConfig
    config = EnvConfig()

    # Then values come from the environment
    assert config.name == "from-env"


def test_envconfig_is_a_backupconfig():
    """Verify EnvConfig is substitutable for BackupConfig."""
    # Given/When an EnvConfig type
    # Then it is a subclass of BackupConfig (so EZBak(EnvConfig()) works)
    assert issubclass(EnvConfig, BackupConfig)


def test_coerce_path_list_empty_string_returns_empty():
    """Verify an empty string yields no paths instead of a phantom cwd entry."""
    # Given an empty string (e.g. an unset EZBAK_STORAGE_PATHS env var)
    # When coercing, then no paths are produced
    assert coerce_path_list("") == []


def test_envconfig_reads_restore_date(monkeypatch):
    """Verify EnvConfig loads EZBAK_RESTORE_DATE into restore_date."""
    # Given the env var set alongside the required fields
    monkeypatch.setenv("EZBAK_NAME", "from-env")
    monkeypatch.setenv("EZBAK_STORAGE_PATHS", "/tmp")  # noqa: S108
    monkeypatch.setenv("EZBAK_RESTORE_DATE", "20250102")

    # When building an EnvConfig
    config = EnvConfig()

    # Then restore_date is populated from the environment
    assert config.restore_date == "20250102"


def test_backupconfig_accepts_region_and_endpoint():
    """Verify BackupConfig accepts the S3 region and endpoint fields."""
    # Given a config with an explicit region and S3-compatible endpoint
    config = BackupConfig(
        name="x",
        aws_s3_bucket_name="my-bucket",
        aws_region="eu-west-1",
        aws_s3_endpoint_url="https://minio.example.com",
    )

    # Then both settings are stored on the config
    assert config.aws_region == "eu-west-1"
    assert config.aws_s3_endpoint_url == "https://minio.example.com"


def test_backupconfig_region_and_endpoint_default_none():
    """Verify region and endpoint default to None so boto3 resolution stays intact."""
    # Given a config with no region or endpoint set
    config = BackupConfig(name="x", aws_s3_bucket_name="my-bucket")

    # Then both are None, deferring to boto3's standard resolution
    assert config.aws_region is None
    assert config.aws_s3_endpoint_url is None


def test_envconfig_reads_region_and_endpoint(monkeypatch):
    """Verify EnvConfig loads EZBAK_AWS_REGION and EZBAK_AWS_S3_ENDPOINT_URL."""
    # Given the S3 env vars set alongside the required fields
    monkeypatch.setenv("EZBAK_NAME", "from-env")
    monkeypatch.setenv("EZBAK_AWS_S3_BUCKET_NAME", "my-bucket")
    monkeypatch.setenv("EZBAK_AWS_REGION", "ap-southeast-2")
    monkeypatch.setenv("EZBAK_AWS_S3_ENDPOINT_URL", "https://minio.example.com")

    # When building an EnvConfig
    config = EnvConfig()

    # Then both settings are populated from the environment
    assert config.aws_region == "ap-southeast-2"
    assert config.aws_s3_endpoint_url == "https://minio.example.com"


def test_coerce_path_list_skips_blank_segments():
    """Verify blank comma segments do not inject a phantom cwd path."""
    # Given a comma list with an empty middle segment
    result = coerce_path_list("/tmp/a,,/tmp/b")  # noqa: S108

    # Then only the two real paths are returned, no cwd entry
    assert result == [Path("/tmp/a"), Path("/tmp/b")]  # noqa: S108


def test_coerce_path_list_strips_whitespace_around_entries():
    """Verify padded comma entries resolve to the real path, not a cwd-relative one."""
    # Given a comma list with spaces around each entry (e.g. "/tmp/a, /tmp/b")
    result = coerce_path_list("/tmp/a, /tmp/b")  # noqa: S108

    # Then each entry is stripped to an absolute path, not Path(" /tmp/b") under cwd
    assert result == [Path("/tmp/a"), Path("/tmp/b")]  # noqa: S108


def test_retention_policy_derivation_keep_last():
    """Verify keep_last yields an active retention policy."""
    # Given a config with keep_last
    config = BackupConfig(name="x", storage_paths=["/tmp"], keep_last=5)  # noqa: S108

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
    # When constructing it
    # Then validation fails rather than accepting the negative count
    with pytest.raises(ValidationError):
        BackupConfig(name="x", storage_paths=["/tmp"], **{field: -1})  # noqa: S108


def test_use_checksums_defaults_true():
    """Verify use_checksums defaults to True."""
    # Given a minimal config with no explicit use_checksums
    config = BackupConfig(name="test", storage_paths=["/tmp/x"])  # noqa: S108

    # Then it defaults to using checksum sidecars
    assert config.use_checksums is True
