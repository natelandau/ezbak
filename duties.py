"""Duty tasks for the project."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from duty import duty
from nclutils import pp

if TYPE_CHECKING:
    from duty.context import Context

PY_SRC_PATHS = (
    Path(x)
    for x in ("src/", "tests/", "duties.py", "scripts/", "dev-templates/")
    if Path(x).exists()
)
PY_SRC_LIST = tuple(str(x) for x in PY_SRC_PATHS)
# Matches how gate-cache reads CI, so a duty skipped in CI is exactly one whose cache is off
CI = os.environ.get("CI", "").strip().lower() not in {"", "0", "false"}
PROJECT_ROOT = Path(__file__).parent
DEV_DIR = PROJECT_ROOT / ".dev"
TEMPLATES_DIR = PROJECT_ROOT / "dev-templates"
# Must stay under a git-ignored directory, or the new file changes the content and voids the pass
TEST_JUNIT_REPORT = ".cache/junit/test.xml"


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


def gated(
    name: str,
    cmd: list[str],
    *,
    paths: tuple[str, ...] = (),
    junit: str | None = None,
) -> list[str]:
    """Wrap a gate command in gate-cache so it skips on content where it already passed.

    Args:
        name (str): Marker name for the gate.
        cmd (list[str]): Command to run.
        paths (tuple[str, ...]): Git pathspecs the command reads. When given, changes outside
            them do not rerun the gate, so every tracked input must be listed.
        junit (str | None): JUnit XML report the command writes, for per-test run stats.

    Returns:
        list[str]: The command prefixed with the gate-cache invocation.
    """
    options = [arg for path in paths for arg in ("--path", path)]
    if junit:
        options += ["--junit", junit]
    return ["gate-cache", "--name", name, *options, "--", *cmd]


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
        ["ruff", "check", "--config", "pyproject.toml", "--no-fix", *PY_SRC_LIST],
        title=pyprefix("code quality check"),
    )


@duty
def format(ctx: Context) -> None:  # ruff:ignore[builtin-variable-shadowing]
    """Format the code with ruff."""
    ctx.run(
        ["ruff", "format", "--check", "--config", "pyproject.toml", *PY_SRC_LIST],
        title=pyprefix("code formatting"),
    )


@duty
def ty(ctx: Context) -> None:
    """Check types with ty."""
    ctx.run(["ty", "check"], title=pyprefix("ty check"))


@duty
def typos(ctx: Context) -> None:
    """Check the code with typos."""
    ctx.run(
        gated("typos", ["typos", "--config", ".typos.toml"]),
        title=pyprefix("typos check"),
    )


@duty
def precommit(ctx: Context) -> None:
    """Run the prek hooks that the other lint duties and the test suite do not cover.

    Runs in CI too, so commits made without local hooks (web edits, Dependabot, --no-verify)
    still get the secret scan and workflow audits.
    """
    ctx.run(
        "SKIP=ruff-check,ruff-format,ty,typos,pytest gate-cache --name prek -- prek run --all-files",
        title=pyprefix("prek hooks"),
    )


@duty(pre=[ruff, format, ty, typos, precommit], capture=CI)
def lint(ctx: Context) -> None:
    """Run all linting duties."""


@duty()
def update_dockerfile(ctx: Context) -> None:
    """Update the Dockerfile with the uv version."""
    dockerfile = PROJECT_ROOT / "Dockerfile"
    version = ctx.run(["uv", "--version"], title="uv version", capture=True)
    version = re.search(r"(\d+\.\d+\.\d+)", version).group(1)
    dockerfile_content = dockerfile.read_text(encoding="utf-8")
    if not re.search(rf"uv:{version}", dockerfile_content):
        dockerfile_content = re.sub(r"uv:\d+\.\d+\.\d+", f"uv:{version}", dockerfile_content)
        dockerfile.write_text(dockerfile_content, encoding="utf-8")
        pp.success(f"Dockerfile updated with uv version: {version}")


@duty(capture=CI, post=[update_dockerfile])
def update(ctx: Context) -> None:
    """Update the project."""
    ctx.run(["uv", "lock", "--upgrade"], title="update uv lock")
    ctx.run(["uv", "sync"], title="sync uv")
    ctx.run(["prek", "autoupdate"], title="prek autoupdate")
    ctx.run(["uvx", "uv-upx", "upgrade", "run"], title="uv-upx upgrade")


@duty
def test(ctx: Context, *cli_args: str) -> None:
    """Test package and generate coverage reports."""
    cmd = [
        "pytest",
        "tests",
        "src",
        "-c",
        "pyproject.toml",
        "--color=yes",
        "--cov",
        "--cov-config=pyproject.toml",
        "--cov-report=xml",
        "--cov-report=term",
        f"--junitxml={TEST_JUNIT_REPORT}",
        *cli_args,
    ]
    # A marker covers the name, not the arguments, so a filtered pass must not skip the full suite
    ctx.run(
        cmd
        if cli_args
        else gated(
            "test",
            cmd,
            paths=("src/", "tests/", "pyproject.toml", "uv.lock", "duties.py"),
            junit=TEST_JUNIT_REPORT,
        ),
        title=pyprefix("Running tests"),
        capture=CI,
    )


@duty()
def dev_clean(ctx: Context) -> None:  # ruff:ignore[unused-function-argument]
    """Clean the development environment."""
    if DEV_DIR.exists():
        shutil.rmtree(DEV_DIR)
        pp.success(f"Cleaned dev env in '{DEV_DIR.name}/'")

    env = PROJECT_ROOT / ".env"
    if env.exists():
        env.unlink()
        pp.success("Cleaned .env file in project root")


@duty(pre=[dev_clean])
def dev_setup(ctx: Context) -> None:  # ruff:ignore[unused-function-argument]
    """Provision a mock development environment."""
    project_1 = DEV_DIR / "source" / "project1"
    project_2 = DEV_DIR / "source" / "project2"
    directories = [
        project_1 / "some_dir",
        project_2 / "some_dir",
        DEV_DIR / "backups",
        DEV_DIR / "logs",
        DEV_DIR / "restore",
    ]
    for directory in directories:
        if not directory.exists():
            directory.mkdir(parents=True)

    filenames = ["foo.txt", "bar.txt", "baz.txt"]
    for directory in [project_1, project_2, project_1 / "some_dir", project_2 / "some_dir"]:
        for filename in filenames:
            file = directory / filename
            if not file.exists():
                file.touch()
    pp.success(f"Development env set up in '{DEV_DIR.name}/'")

    # copy eztest.py to .dev/eztest.py
    eztest_template = TEMPLATES_DIR / "eztest.py"
    eztest = DEV_DIR / "eztest.py"
    shutil.copy2(eztest_template, eztest)
    pp.success(f"eztest.py file created in '{DEV_DIR.name}/{eztest.name}'")

    # copy .env.template to .env
    env_template = TEMPLATES_DIR / ".env.template"
    env = PROJECT_ROOT / ".env"
    shutil.copy2(env_template, env)
    pp.success(f".env file created in '{PROJECT_ROOT.name}/{env.name}'")

    pp.success(
        "✓ Development environment setup complete.\n  Start the development environment with one of the following commands:\n    [green]docker compose up --build[/green]\n    [green]uv run -m ezbak.container[/green]"
    )
