"""Run operator-configured lifecycle hooks around container backup and restore runs."""

from __future__ import annotations

import subprocess  # noqa: S404

from loguru import logger


def _log_hook_output(phase: str, stdout: str | None, stderr: str | None, *, error: bool) -> None:
    """Log a hook's captured stdout/stderr at error level on failure, else debug."""
    log = logger.error if error else logger.debug
    if stdout:
        log(f"{phase} hook stdout: {stdout}")
    if stderr:
        log(f"{phase} hook stderr: {stderr}")


def run_hook(command: str | None, *, phase: str, timeout: int) -> bool:
    """Run a configured lifecycle hook command, reporting whether it succeeded.

    Execute an operator-supplied shell command around a backup or restore so a
    container can quiesce a source before archiving it or clean up afterward. The
    command runs through ``/bin/sh -c`` and inherits the container environment, so it
    sees the ``EZBAK_`` variables and supports pipes, ``&&``, and quoting.

    Args:
        command (str | None): The shell command to run. ``None`` or blank is a no-op.
        phase (str): Short label for the hook point (e.g. "pre-backup"), used in logs.
        timeout (int): Seconds before the hook is killed. ``0`` disables the timeout.

    Returns:
        bool: True when the hook is a no-op or exits zero; False on a non-zero exit,
            a timeout, or a failure to spawn the process.
    """
    if not command or not command.strip():
        return True

    logger.info(f"Running {phase} hook: {command}")
    try:
        # timeout or None: a configured 0 means "run to completion" (subprocess treats
        # None as no timeout). shell=False; the shell is the explicit `/bin/sh -c` argv.
        result = subprocess.run(  # noqa: S603
            ["/bin/sh", "-c", command],
            timeout=timeout or None,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        logger.error(f"{phase} hook timed out after {timeout}s and was killed: {command}")
        # mypy's stdlib stubs declare TimeoutExpired.stdout/stderr as bytes | None
        # regardless of text=True; at runtime they are str here since text=True above.
        _log_hook_output(phase, e.stdout, e.stderr, error=True)  # type: ignore[arg-type]
        return False
    except OSError as e:
        logger.error(f"{phase} hook could not be started: {e}")
        return False

    if result.returncode != 0:
        logger.error(f"{phase} hook failed with exit code {result.returncode}: {command}")
        _log_hook_output(phase, result.stdout, result.stderr, error=True)
        return False

    _log_hook_output(phase, result.stdout, result.stderr, error=False)
    logger.debug(f"{phase} hook succeeded")
    return True
