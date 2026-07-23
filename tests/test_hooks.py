"""Test the lifecycle hook runner."""

from __future__ import annotations

import subprocess  # ruff:ignore[suspicious-subprocess-import]

import pytest

from ezbak.hooks import run_hook


@pytest.mark.parametrize("command", [None, "", "   "])
def test_run_hook_noop_returns_true_without_running(command, mocker):
    """Verify an unset or blank hook is a no-op that spawns no process."""
    # Given a spy on subprocess.run
    spy = mocker.patch("ezbak.hooks.subprocess.run", autospec=True)

    # When running an empty hook
    result = run_hook(command, phase="pre-backup", timeout=300)

    # Then it succeeds and nothing was spawned
    assert result is True
    spy.assert_not_called()


def test_run_hook_success_runs_command(tmp_path):
    """Verify a zero-exit hook runs the command and returns True."""
    # Given a marker path the command will create
    marker = tmp_path / "ran"

    # When running a hook that touches the marker
    result = run_hook(f"touch {marker}", phase="pre-backup", timeout=300)

    # Then it succeeds and the command actually ran
    assert result is True
    assert marker.exists()


def test_run_hook_nonzero_exit_returns_false():
    """Verify a non-zero exit is reported as a failed hook."""
    # Given a command that exits non-zero
    # When running it
    result = run_hook("exit 3", phase="post-backup", timeout=300)

    # Then the hook is a failure
    assert result is False


def test_run_hook_timeout_returns_false(mocker):
    """Verify a hook that times out is killed and reported as a failure."""
    # Given subprocess.run raising a timeout
    mocker.patch(
        "ezbak.hooks.subprocess.run",
        autospec=True,
        side_effect=subprocess.TimeoutExpired(cmd="sh", timeout=1),
    )

    # When running the hook
    result = run_hook("sleep 5", phase="pre-backup", timeout=1)

    # Then it is a failure
    assert result is False


def test_run_hook_spawn_error_returns_false(mocker):
    """Verify a process that cannot be spawned is reported as a failure."""
    # Given subprocess.run raising OSError
    mocker.patch("ezbak.hooks.subprocess.run", autospec=True, side_effect=OSError("boom"))

    # When running the hook
    result = run_hook("whatever", phase="pre-restore", timeout=300)

    # Then it is a failure
    assert result is False


def test_run_hook_supports_shell_operators(tmp_path):
    """Verify a hook command runs through a shell so pipes and && work."""
    # Given a marker path and a command that uses a pipe and &&
    marker = tmp_path / "shell_ops"

    # When running a hook that pipes and chains commands
    result = run_hook(f"echo hi | tr a-z A-Z > {marker} && true", phase="pre-backup", timeout=300)

    # Then the shell interpreted the operators and the hook succeeded
    assert result is True
    assert marker.read_text().strip() == "HI"


def test_run_hook_timeout_zero_disables_timeout(mocker):
    """Verify timeout=0 passes no timeout to subprocess.run."""
    # Given a spy on subprocess.run returning a clean exit
    spy = mocker.patch("ezbak.hooks.subprocess.run", autospec=True)
    spy.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    # When running a hook with timeout 0
    run_hook("true", phase="pre-backup", timeout=0)

    # Then subprocess.run was called with timeout=None
    assert spy.call_args.kwargs["timeout"] is None


def test_run_hook_non_utf8_output_still_succeeds():
    """Verify a zero-exit hook that prints non-UTF-8 bytes is not turned into a crash."""
    # Given a hook that exits 0 but writes an invalid UTF-8 byte to stdout
    # When running it
    result = run_hook(r"printf '\377'", phase="post-backup", timeout=300)

    # Then the successful exit is honored instead of raising UnicodeDecodeError
    assert result is True
