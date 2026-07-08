# ezbak

Backup tool: tar.gz archives with local + S3 destinations and retention policies. One codebase, three interfaces (Python library, CLI, Docker container).

## Architecture

One typed config schema, one core class, thin adapters.

- `config.py` — `BackupConfig` (pydantic `BaseModel`): the **sole** option schema. Library callers construct it directly; it does **not** read the environment.
- `env.py` — `EnvConfig(BackupConfig, BaseSettings)`: loads `EZBAK_`-prefixed env + `.env`/`.env.secrets`. Used only by the CLI and container.
- `core.py` — `EZBak`: the one public class. `EZBak(BackupConfig(...))` is primary; `ezbak(**kwargs)` is a thin shortcut. Owns the temp staging dir; derives backends from destinations.
- `cli.py` + `cli_commands/` — cappa CLI. `build_config()` maps parsed args to a config.
- `container.py` — Docker entrypoint (env → `EnvConfig` → `EZBak`) with an APScheduler cron loop.
- `storage/` — `base.py` (ABC), `local.py`, `s3.py`, `aws.py`.
- Other modules: `backup.py` (`Backup`, `StorageLocation`), `naming.py`, `retention.py`, `filters.py`, `logging.py`, `constants.py`.

Backends follow the destinations: `storage_paths` gives local, `aws_s3_bucket_name` gives S3, both give both. There is no `storage_type` selector.

## Commands

uv only.

- Test: `uv run pytest` (runs with `--doctest-modules`, so every module and docstring example must import/run cleanly)
- Lint/format: `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/`
- Types: `uv run mypy --config-file=pyproject.toml src/`

Tool config (ruff, mypy, pytest, coverage) lives in `pyproject.toml`.

## Conventions

- Commit subject max **50** chars (the `committed` pre-commit hook enforces this, not 70); body lines wrap at 72.
- `CHANGELOG.md` is commitizen-managed and regenerated on `cz bump`. Never hand-edit it, including for breaking changes; the release tooling owns it.
- Version source is `constants.py:__version__` (commitizen `version_files`).

## Gotchas

- A bare `EnvConfig()` runs full validation (requires `name` + a destination). The CLI builds `EnvConfig(**cli_values, _env_file=None)` so the environment only fills fields with no flag (tz, AWS creds).
- The container reads `.env`/`.env.secrets`, so running it locally picks up real S3 credentials.
