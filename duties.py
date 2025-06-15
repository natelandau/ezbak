"""Duty tasks for the project."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from duty import duty, tools
from nclutils import console

if TYPE_CHECKING:
    from duty.context import Context

PY_SRC_PATHS = (Path(x) for x in ("src/", "tests/", "duties.py", "scripts/") if Path(x).exists())
PY_SRC_LIST = tuple(str(x) for x in PY_SRC_PATHS)
CI = os.environ.get("CI", "0") in {"1", "true", "yes", ""}
DEV_DIR = Path(__file__).parent / "tmp" / "fixtures"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string.

    Args:
        text (str): String to remove ANSI escape sequences from.

    Returns:
        str: String without ANSI escape sequences.
    """
    ansi_chars = re.compile(r"(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]")

    # Replace [ with \[ so rich doesn't interpret output as style tags
    return ansi_chars.sub("", text).replace("[", r"\[")


def pyprefix(title: str) -> str:
    """Add a prefix to the title if CI is true.

    Returns:
        str: Title with prefix if CI is true.
    """
    if CI:
        prefix = f"(python{sys.version_info.major}.{sys.version_info.minor})"
        return f"{prefix:14}{title}"
    return title


@duty(silent=True)
def clean(ctx: Context) -> None:
    """Clean the project."""
    ctx.run("rm -rf .cache")
    ctx.run("rm -rf build")
    ctx.run("rm -rf dist")
    ctx.run("rm -rf pip-wheel-metadata")
    ctx.run("find . -type d -name __pycache__ | xargs rm -rf")
    ctx.run("find . -name '.DS_Store' -delete")


@duty
def ruff(ctx: Context) -> None:
    """Check the code quality with ruff."""
    ctx.run(
        tools.ruff.check(*PY_SRC_LIST, fix=False, config="pyproject.toml"),
        title=pyprefix("code quality check"),
        command="ruff check --config pyproject.toml --no-fix src/",
    )


@duty
def format(ctx: Context) -> None:  # noqa: A001
    """Format the code with ruff."""
    ctx.run(
        tools.ruff.format(*PY_SRC_LIST, check=True, config="pyproject.toml"),
        title=pyprefix("code formatting"),
        command="ruff format --check --config pyproject.toml src/",
    )


@duty
def mypy(ctx: Context) -> None:
    """Check the code with mypy."""
    os.environ["FORCE_COLOR"] = "1"
    ctx.run(
        tools.mypy("src/", config_file="pyproject.toml"),
        title=pyprefix("mypy check"),
        command="mypy --config-file pyproject.toml src/",
    )


@duty
def typos(ctx: Context) -> None:
    """Check the code with typos."""
    ctx.run(
        ["typos", "--config", ".typos.toml"],
        title=pyprefix("typos check"),
        command="typos --config .typos.toml",
    )


@duty(skip_if=CI, skip_reason="skip pre-commit in CI environments")
def precommit(ctx: Context) -> None:
    """Run pre-commit hooks."""
    ctx.run(
        "SKIP=mypy,pytest,ruff pre-commit run --all-files",
        title=pyprefix("pre-commit hooks"),
    )


@duty(pre=[ruff, mypy, typos, precommit], capture=CI)
def lint(ctx: Context) -> None:
    """Run all linting duties."""


@duty(capture=CI)
def update(ctx: Context) -> None:
    """Update the project."""
    ctx.run(["uv", "lock", "--upgrade"], title="update uv lock")
    ctx.run(["pre-commit", "autoupdate"], title="pre-commit autoupdate")


@duty
def test(ctx: Context, *cli_args: str) -> None:
    """Test package and generate coverage reports."""
    ctx.run(
        tools.pytest(
            "tests",
            "src",
            config_file="pyproject.toml",
            color="yes",
        ).add_args(
            "--cov",
            "--cov-config=pyproject.toml",
            "--cov-report=xml",
            "--cov-report=term",
            *cli_args,
        ),
        title=pyprefix("Running tests"),
        capture=CI,
    )


@duty()
def dev_clean(ctx: Context) -> None:
    """Clean the development environment."""
    if DEV_DIR.exists():
        ctx.run(["rm", "-rf", str(DEV_DIR)], title="Clean dev env")


@duty(pre=[dev_clean])
def dev_setup(ctx: Context) -> None:  # noqa: ARG001
    """Provision a mock development environment."""
    destinations = [DEV_DIR / "dest1", DEV_DIR / "dest2"]
    for destination in destinations:
        if not destination.exists():
            destination.mkdir(parents=True)

    sources = [DEV_DIR / "source1", DEV_DIR / "source2"]
    for source in sources:
        if not source.exists():
            source.mkdir(parents=True)

    for source in sources:
        files = [source / "foo.txt", source / "bar.txt", source / "baz.txt"]
        for file in files:
            file.touch()

    console.print(f"✓ Development env set up in {DEV_DIR}")
