"""Test the ezbak CLI."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
import cappa
import pytest
import time_machine

# cappa.command, .invoke, and .parse share names with cappa submodules, which type checkers
# resolve instead of the functions; cappa.base exports the functions themselves
from cappa.base import invoke, parse

from ezbak.cli import CreateCommand, EZBakCLI, build_config
from ezbak.constants import DEFAULT_DATE_FORMAT, LogLevel
from ezbak.logging import instantiate_logger
from tests.helpers import FIXTURE_ARCHIVE, LEGACY_BACKUP_NAMES

UTC = ZoneInfo("UTC")
frozen_time = datetime(2025, 6, 9, tzinfo=UTC)
frozen_time_str = frozen_time.strftime(DEFAULT_DATE_FORMAT)


@time_machine.travel(frozen_time, tick=False)
def test_cli_create_backup(filesystem):
    """Verify create writes the backup to every storage path."""
    # Given: Source and destination directories from fixture
    src_dir, dest1, dest2 = filesystem

    # When: creating a backup to two storage paths
    invoke(
        obj=EZBakCLI,
        argv=[
            "create",
            "--name",
            "test",
            "--source",
            str(src_dir),
            "--storage",
            str(dest1),
            "--storage",
            str(dest2),
        ],
    )

    # Then: both storage paths hold the archive
    filename = f"test-{frozen_time_str}.tgz"
    assert (dest1 / filename).exists()
    assert (dest2 / filename).exists()


def test_cli_create_write_failure_exits_nonzero(filesystem, mocker):
    """Verify create exits non-zero and stores nothing when the archive cannot be written."""
    # Given: a storage path whose write fails
    src_dir, dest1, _ = filesystem
    mocker.patch("ezbak.storage.local.copy_with_periodic_fsync", side_effect=OSError("disk full"))

    # When: creating a backup
    with pytest.raises(cappa.Exit) as exc:
        invoke(
            obj=EZBakCLI,
            argv=["create", "--name", "test", "--source", str(src_dir), "--storage", str(dest1)],
        )

    # Then: the command fails and no archive is left behind
    assert exc.value.code == 1
    assert not list(dest1.glob("*.tgz"))


def test_cli_prune_backups_dry_run(mocker, capsys, legacy_backups):
    """Verify a dry-run prune previews targets without prompting or deleting."""
    # Given: a confirmation spy that must never be reached in dry-run mode
    confirm = mocker.patch("ezbak.cli_commands.prune.Confirm.ask", return_value=True)

    # When: pruning runs with --dry-run
    invoke(
        obj=EZBakCLI,
        argv=[
            "prune",
            "--name",
            "test",
            "--storage",
            str(legacy_backups),
            "--keep-last",
            "3",
            "--dry-run",
        ],
    )
    output = capsys.readouterr().err

    # Then: no confirmation is requested, targets are previewed, and all files remain
    confirm.assert_not_called()
    assert "Would delete 10 backups" in output
    assert "Pruned" not in output
    assert len(list(legacy_backups.iterdir())) == len(LEGACY_BACKUP_NAMES)


def test_cli_prune_backups_dry_run_no_targets(mocker, capsys, tmp_path):
    """Verify a dry run that targets nothing never claims a deletion happened."""
    # Given: a confirmation spy that must never be reached in dry-run mode
    confirm = mocker.patch("ezbak.cli_commands.prune.Confirm.ask", return_value=True)

    # Given: fewer backups than the retention policy keeps, so nothing would be pruned
    for filename in [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095745-minutely.tgz",
    ]:
        Path(tmp_path / filename).touch()

    # When: pruning runs with --dry-run and a policy that keeps more than exist
    invoke(
        obj=EZBakCLI,
        argv=[
            "prune",
            "--name",
            "test",
            "--storage",
            str(tmp_path),
            "--keep-last",
            "100",
            "--dry-run",
        ],
    )
    output = capsys.readouterr().err

    # Then: no prompt fires and the output never uses the past-tense "No backups deleted"
    confirm.assert_not_called()
    assert "No backups would be deleted" in output
    assert "No backups deleted" not in output


def test_cli_prune_backups_force_skips_confirmation(mocker, capsys, legacy_backups):
    """Verify --force prunes to the retention policy without asking for confirmation."""
    # Given: a confirmation that would decline if it were ever reached
    confirm = mocker.patch("ezbak.cli_commands.prune.Confirm.ask", return_value=False)

    # When: pruning runs with --force
    invoke(
        obj=EZBakCLI,
        argv=[
            "prune",
            "--name",
            "test",
            "--storage",
            str(legacy_backups),
            "--keep-last",
            "3",
            "--force",
        ],
    )
    output = capsys.readouterr().err

    # Then: no confirmation is requested and only the three newest backups remain
    confirm.assert_not_called()
    assert "Deleted 10 backups" in output
    assert len(list(legacy_backups.iterdir())) == 3


def test_cli_prune_declined_confirmation_deletes_nothing(mocker, legacy_backups):
    """Verify declining the prune confirmation leaves every backup in place."""
    # Given: an operator who answers no at the prompt
    mocker.patch("ezbak.cli_commands.prune.Confirm.ask", return_value=False)

    # When: pruning runs without --force
    invoke(
        obj=EZBakCLI,
        argv=["prune", "--name", "test", "--storage", str(legacy_backups), "--keep-last", "3"],
    )

    # Then: nothing is deleted
    assert len(list(legacy_backups.iterdir())) == len(LEGACY_BACKUP_NAMES)


def test_cli_list_backups(capsys, tmp_path):
    """Verify listing backups."""
    # Given: three backups in a storage path
    filenames = [
        "test-20250609T101857-hourly.tgz",
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095804-minutely.tgz",
    ]
    for filename in filenames:
        Path(tmp_path / filename).touch()

    # When: listing backups
    invoke(
        obj=EZBakCLI,
        argv=["list", "--name", "test", "--storage", str(tmp_path)],
    )
    output = capsys.readouterr().err

    # Then: every backup is listed
    for filename in filenames:
        assert filename in output


def test_cli_list_backups_all_storage(s3_bucket: str, capsys, tmp_path):
    """Verify listing shows both local and S3 backups when both destinations have backups."""
    # Given: one backup in a local path and another in the bucket
    local_name = "test-20250609T101857-hourly.tgz"
    s3_name = "test-20240609T000000-yearly.tgz"
    (tmp_path / local_name).touch()
    boto3.client("s3").put_object(Bucket=s3_bucket, Key=s3_name, Body=b"archive")

    # When: listing backups across both destinations
    invoke(
        obj=EZBakCLI,
        argv=["list", "--name", "test", "--storage", str(tmp_path), "--s3-bucket", s3_bucket],
    )
    output = capsys.readouterr().err

    # Then: both backups appear
    assert local_name in output
    assert s3_name in output


@pytest.mark.parametrize("bucket_missing", [False, True], ids=["listing-denied", "bucket-missing"])
def test_cli_list_backups_unusable_s3_destination_exits_nonzero(
    s3_bucket: str, break_s3_listing, capsys, tmp_path, bucket_missing
) -> None:
    """Verify list still prints the local backups but exits non-zero when S3 cannot be read."""
    # Given: a local backup alongside a bucket that cannot be listed or does not exist
    local_name = "test-20250609T101857-hourly.tgz"
    (tmp_path / local_name).touch()
    if bucket_missing:
        bucket = "missing-bucket"
    else:
        bucket = s3_bucket
        break_s3_listing()

    # When: listing backups
    with pytest.raises(cappa.Exit) as exc:
        invoke(
            obj=EZBakCLI,
            argv=["list", "--name", "test", "--storage", str(tmp_path), "--s3-bucket", bucket],
        )

    # Then: it exits non-zero and still lists what was found
    assert exc.value.code == 1
    assert local_name in capsys.readouterr().err


def test_cli_list_backups_partial_index_with_no_backups_found(
    s3_bucket: str, break_s3_listing, capsys, tmp_path
) -> None:
    """Verify an unreadable destination with nothing found never prints "No backups found"."""
    # Given an empty local storage path alongside an S3 bucket that cannot be listed
    break_s3_listing()

    # When listing backups
    with pytest.raises(cappa.Exit) as exc:
        invoke(
            obj=EZBakCLI,
            argv=[
                "list",
                "--name",
                "test",
                "--storage",
                str(tmp_path),
                "--s3-bucket",
                s3_bucket,
            ],
        )
    output = capsys.readouterr().err

    # Then it exits non-zero and never claims there are no backups when it could not look
    assert exc.value.code == 1
    assert "No backups found" not in output
    assert f"S3 bucket '{s3_bucket}'" in output


def test_cli_list_backups_empty_and_readable_prints_no_backups_found(capsys, tmp_path) -> None:
    """Verify a fully readable but empty configuration still reports no backups found."""
    # Given an empty, readable local storage path and no S3 destination configured

    # When listing backups, then it does not raise cappa.Exit
    invoke(
        obj=EZBakCLI,
        argv=["list", "--name", "test", "--storage", str(tmp_path)],
    )
    output = capsys.readouterr().err

    # Then it reports no backups found rather than staying silent or exiting non-zero
    assert "No backups found" in output


def test_cli_restore_backup(filesystem, restore_dir):
    """Verify that a backup is restored correctly."""
    # Given: a backup in the storage path
    _, dest1, _ = filesystem
    shutil.copy2(FIXTURE_ARCHIVE, dest1 / f"test-{frozen_time_str}-yearly.tgz")

    # When: restoring it
    invoke(
        obj=EZBakCLI,
        argv=[
            "restore",
            "--name",
            "test",
            "--storage",
            str(dest1),
            "--restore-path",
            str(restore_dir),
        ],
    )

    # Then: the archive contents are on disk
    assert (restore_dir / "src" / "baz.txt").exists()


def test_cli_restore_corrupt_archive_exits_nonzero(filesystem, restore_dir):
    """Verify restoring an archive that is not a tarball exits non-zero and restores nothing."""
    # Given: a backup whose bytes are not a tarball
    _, dest1, _ = filesystem
    (dest1 / "test-20250103T090000.tgz").write_bytes(b"not a tarball")

    # When: restoring it
    with pytest.raises(cappa.Exit) as exc:
        invoke(
            obj=EZBakCLI,
            argv=[
                "restore",
                "--name",
                "test",
                "--storage",
                str(dest1),
                "--restore-path",
                str(restore_dir),
            ],
        )

    # Then: the command fails and the restore path is still empty
    assert exc.value.code == 1
    assert not list(restore_dir.iterdir())


def test_build_config_reads_s3_bucket(monkeypatch, filesystem):
    """Verify the CLI builder wires an S3 bucket into the config."""
    # Given credentials in the environment and a bucket flag
    monkeypatch.setenv("EZBAK_AWS_ACCESS_KEY", "AKIA_TEST")
    monkeypatch.setenv("EZBAK_AWS_SECRET_KEY", "secret_test")
    src, dest1, _ = filesystem

    # When building a config for a create command targeting S3
    cli = EZBakCLI(
        command=CreateCommand(sources=[src]),
        name="t",
        storage_paths=[dest1],
        s3_bucket="my-bucket",
    )
    config = build_config(cli)

    # Then the bucket and env-sourced credentials are present
    assert config.aws_s3_bucket_name == "my-bucket"
    assert config.aws_access_key == "AKIA_TEST"


def test_s3_only_cli_parses_without_storage(monkeypatch, tmp_path):
    """Verify an S3-only backup parses without the --storage flag."""
    # Given S3 credentials in the environment
    monkeypatch.setenv("EZBAK_AWS_ACCESS_KEY", "AKIA_TEST")
    monkeypatch.setenv("EZBAK_AWS_SECRET_KEY", "secret_test")

    # When parsing a create command with only --s3-bucket (no --storage)
    cli = parse(
        EZBakCLI,
        argv=["--name", "t", "--s3-bucket", "my-bucket", "create", "--source", str(tmp_path)],
    )
    config = build_config(cli)

    # Then parsing succeeds and the config has no local paths but the S3 bucket
    assert config.storage_paths == []
    assert config.aws_s3_bucket_name == "my-bucket"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param(
            ["create", "--source", "{data}", "--no-use-checksums"],
            {"use_checksums": False},
            id="create-no-use-checksums",
        ),
        pytest.param(
            ["restore", "--restore-path", "{data}", "--no-use-checksums"],
            {"use_checksums": False},
            id="restore-no-use-checksums",
        ),
        pytest.param(
            ["create", "--source", "{data}", "--sqlite-path", "{data}/app.db"],
            {"sqlite_paths": [Path("{data}/app.db")]},
            id="sqlite-path",
        ),
        pytest.param(
            [
                "create",
                "--source",
                "{data}",
                "--sqlite-path",
                "{data}/a.db",
                "--sqlite-path",
                "{data}/b.db",
            ],
            {"sqlite_paths": [Path("{data}/a.db"), Path("{data}/b.db")]},
            id="sqlite-path-repeated",
        ),
        # Stored unexpanded, since expansion happens per backup run
        pytest.param(
            ["create", "--source", "{data}", "--sqlite-path", "{data}/*.db"],
            {"sqlite_paths": [Path("{data}/*.db")]},
            id="sqlite-path-glob",
        ),
        # Stays relative, to be anchored to each source path at expansion time
        pytest.param(
            ["create", "--source", "{data}", "--sqlite-path", "**/*.db"],
            {"sqlite_paths": [Path("**/*.db")]},
            id="sqlite-path-relative",
        ),
        pytest.param(
            ["restore", "--restore-path", "{data}", "--restore-date", "20250102"],
            {"restore_date": "20250102"},
            id="restore-date",
        ),
        pytest.param(
            ["restore", "--restore-path", "{data}", "--skip-if-no-backup"],
            {"skip_if_no_backup": True},
            id="skip-if-no-backup",
        ),
        pytest.param(
            ["restore", "--restore-path", "{data}", "--skip-if-populated"],
            {"skip_restore_if_populated": True},
            id="skip-if-populated",
        ),
        pytest.param(
            ["prune", "--keep-last", "3", "--keep-daily", "4"],
            {"keep_last": 3, "keep_daily": 4},
            id="keep-last-and-daily",
        ),
        pytest.param(
            ["--s3-region", "eu-west-1", "create", "--source", "{data}"],
            {"aws_region": "eu-west-1"},
            id="s3-region",
        ),
        pytest.param(
            ["--s3-endpoint-url", "https://minio.example.com", "create", "--source", "{data}"],
            {"aws_s3_endpoint_url": "https://minio.example.com"},
            id="s3-endpoint-url",
        ),
    ],
)
def test_build_config_maps_flags(tmp_path, argv: list[str], expected: dict[str, object]):
    """Verify each command-line flag lands on its config field."""
    # Given a parsed command line; a glob's root directory must exist to validate
    data = tmp_path / "data"
    data.mkdir()
    cli = parse(
        EZBakCLI,
        argv=["--name", "test", "--storage", str(data), *(x.format(data=data) for x in argv)],
    )

    # When building the config
    config = build_config(cli)

    # Then each field holds the flag's value
    def _fill(value: object) -> object:
        if isinstance(value, list):
            return [Path(str(x).format(data=data)) for x in value]
        return value

    assert {field: getattr(config, field) for field in expected} == {
        field: _fill(value) for field, value in expected.items()
    }


def test_cli_invalid_config_exits_cleanly(capsys):
    """Verify a config with no storage location exits non-zero with a logged message."""
    # Given a logger bound to this test's stderr and no storage configured
    instantiate_logger(LogLevel.INFO)

    # When invoking a command with a name but no storage destination
    with pytest.raises(cappa.Exit) as exc:
        invoke(obj=EZBakCLI, argv=["list", "--name", "test"])

    # Then it exits non-zero with a helpful message instead of a raw pydantic traceback
    assert exc.value.code == 1
    assert "No storage configured" in capsys.readouterr().err


def test_cli_restore_skip_if_no_backup_is_noop(filesystem, capsys, restore_dir):
    """Verify restore --skip-if-no-backup exits cleanly when no backup exists yet."""
    # Given an empty storage path, a fresh deployment with no backup to restore
    _, dest1, _ = filesystem

    # When restoring with --skip-if-no-backup and no backup present, then it does not raise
    invoke(
        obj=EZBakCLI,
        argv=[
            "restore",
            "--name",
            "test",
            "--storage",
            str(dest1),
            "--restore-path",
            str(restore_dir),
            "--skip-if-no-backup",
        ],
    )

    # Then it reports the no-op instead of failing the restore
    output = capsys.readouterr().err
    assert "nothing to restore" in output
    assert "Backup restored" not in output


def test_cli_restore_skip_if_no_backup_date_miss_is_noop(filesystem, capsys, restore_dir):
    """Verify restore --skip-if-no-backup is a clean no-op on a restore-date miss, without an error log."""
    # Given a backup exists but none is old enough for the requested date
    _, dest1, _ = filesystem
    shutil.copy2(FIXTURE_ARCHIVE, dest1 / "test-20250103T090000.tgz")

    # When restoring as of 2024 (before the only backup) with --skip-if-no-backup, it does not raise
    invoke(
        obj=EZBakCLI,
        argv=[
            "restore",
            "--name",
            "test",
            "--storage",
            str(dest1),
            "--restore-path",
            str(restore_dir),
            "--restore-date",
            "2024",
            "--skip-if-no-backup",
        ],
    )

    # Then it is a clean no-op with no error line on a successful run
    output = capsys.readouterr().err
    assert "nothing to restore" in output
    assert "ERROR" not in output


def test_cli_restore_backup_by_date(filesystem, capsys, restore_dir):
    """Verify restore --restore-date restores the point-in-time backup."""
    # Given two backups on different days in one storage location
    _, dest1, _ = filesystem
    for ts in ("20250101T120000", "20250103T090000"):
        shutil.copy2(FIXTURE_ARCHIVE, dest1 / f"test-{ts}.tgz")

    # When restoring as of 2025-01-02 (only the Jan 1 backup qualifies).
    # -v raises the log level to DEBUG so the selection line is emitted.
    invoke(
        obj=EZBakCLI,
        argv=[
            "restore",
            "-v",
            "--name",
            "test",
            "--storage",
            str(dest1),
            "--restore-path",
            str(restore_dir),
            "--restore-date",
            "20250102",
        ],
    )
    output = capsys.readouterr().err

    # Then a restore happens and the point-in-time backup was selected
    assert "Backup restored to 'restore'" in output
    assert "Selected backup as of 20250102: test-20250101T120000.tgz" in output


def test_cli_restore_by_date_no_match_exits_nonzero(filesystem, restore_dir):
    """Verify restore --restore-date exits non-zero when nothing qualifies."""
    # Given a single 2025 backup
    _, dest1, _ = filesystem
    shutil.copy2(FIXTURE_ARCHIVE, dest1 / "test-20250103T090000.tgz")

    # When restoring as of a year before it
    # Then the CLI exits non-zero
    with pytest.raises(cappa.Exit) as exc:
        invoke(
            obj=EZBakCLI,
            argv=[
                "restore",
                "--name",
                "test",
                "--storage",
                str(dest1),
                "--restore-path",
                str(restore_dir),
                "--restore-date",
                "2024",
            ],
        )
    assert exc.value.code == 1


def test_cli_prune_unreadable_destination_exits_zero(s3_bucket, break_s3_listing, capsys, tmp_path):
    """Verify prune skips an unreadable destination, exits zero, and prunes the healthy one."""
    # Given three archives in a local path and the same three in a bucket that cannot be listed
    names = [
        "test-20250609T095745-minutely.tgz",
        "test-20250609T095804-minutely.tgz",
        "test-20250609T101857-hourly.tgz",
    ]
    s3 = boto3.client("s3")
    for name in names:
        Path(tmp_path / name).touch()
        s3.put_object(Bucket=s3_bucket, Key=name, Body=b"archive")
    break_s3_listing()

    # When pruning down to the newest backup, then prune does not exit non-zero
    invoke(
        obj=EZBakCLI,
        argv=[
            "prune",
            "--name",
            "test",
            "--storage",
            str(tmp_path),
            "--s3-bucket",
            s3_bucket,
            "--keep-last",
            "1",
            "--force",
        ],
    )
    output = capsys.readouterr().err

    # Then the healthy destination is pruned, the unreadable one keeps every archive,
    # and the skipped destination is named in the log
    assert [x.name for x in tmp_path.iterdir()] == ["test-20250609T101857-hourly.tgz"]
    assert {x["Key"] for x in s3.list_objects_v2(Bucket=s3_bucket)["Contents"]} == set(names)
    assert f"Could not index S3 bucket '{s3_bucket}'" in output
