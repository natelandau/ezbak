"""Duty tasks for the project."""

from __future__ import annotations

import os
import re
import shutil
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
PROJECT_ROOT = Path(__file__).parent
DEV_DIR = PROJECT_ROOT / ".dev"
TEMPLATES_DIR = PROJECT_ROOT / "dev-templates"


def replace_in_file(file_path: str | Path, replacements: dict[str, str]) -> bool:
    """Replace text in a file with a dictionary of replacements.

    Args:
        file_path (str): Path to the file to replace text in.
        replacements (dict[str, str]): Dictionary of old text to new text.

    Returns:
        bool: True if the replacements were successful, False otherwise.
    """
    path = Path(file_path) if not isinstance(file_path, Path) else file_path
    try:
        if not path.exists():
            console.print(f"File {file_path} does not exist")
            return False

        content = path.read_text(encoding="utf-8")

        for old_text, new_text in replacements.items():
            content = content.replace(old_text, new_text)

        path.write_text(content, encoding="utf-8")

    except Exception as e:  # noqa: BLE001
        console.print(f"Error processing {file_path}: {e}")
        return False

    return True


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
def dev_clean(ctx: Context) -> None:  # noqa: ARG001
    """Clean the development environment."""
    if DEV_DIR.exists():
        shutil.rmtree(DEV_DIR)

    env = PROJECT_ROOT / ".env"
    if env.exists():
        env.unlink()

    console.print(f"✓ Cleaned dev env in '{DEV_DIR.name}/'")


@duty(pre=[dev_clean])
def dev_setup(ctx: Context) -> None:  # noqa: ARG001
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
    console.print(f"✓ Development env set up in '{DEV_DIR.name}/'")

    # copy .env.template to .env
    env_template = TEMPLATES_DIR / ".env.template"
    env = PROJECT_ROOT / ".env"
    shutil.copy2(env_template, env)
    console.print(f"✓ .env file created in '{PROJECT_ROOT.name}/{env.name}'")

    # copy docker-compose.override.yml to .dev/docker-compose.override.yml
    docker_compose_template = TEMPLATES_DIR / "docker-compose.template.yml"
    docker_compose = DEV_DIR / "docker-compose.yml"
    shutil.copy2(docker_compose_template, docker_compose)
    console.print(f"✓ Docker compose file created in '{DEV_DIR.name}/{docker_compose.name}'")
    replacements = {"_ROOT_DIR_": str(PROJECT_ROOT)}

    if not replace_in_file(docker_compose, replacements):
        console.print(f"Error replacing text in '{DEV_DIR.name}/{docker_compose.name}'")
        return
    console.print(f"✓ Replaced text in '{DEV_DIR.name}/{docker_compose.name}'")

    console.print(
        f"✓ Development environment setup complete. Start the development environment with\n  one of the following commands:\n    [green]docker compose -f {DEV_DIR.name}/{docker_compose.name} up --build[/green]\n    [green]uv run -m ezbak.entrypoint[/green]"
    )
