# ezbak

Backup tool: tar.gz archives with local + S3 destinations and retention policies. One codebase, three interfaces (Python library, CLI, Docker container).

## Primary purpose

ezbak exists to move shared state between jobs and hosts in an orchestrated setting (Nomad, Kubernetes, etc.). **The container is the primary surface.** The canonical deployment is three cooperating tasks around a job:

- a **sidecar** taking cron-based backups while the job runs,
- a **post-stop** task taking a final backup before the orchestrator clears the job, and
- a **pre-start** task that fetches the most recent backup and stages it on the target host before the job starts.

The CLI and Python library are conveniences: ezbak happens to be an extensible backup manager, so they are exposed, but they are not the design center. ezbak is **not** trying to compete with a feature-complete backup tool. Weigh design and feature decisions against the orchestrated container workflow first.

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

- Ruff runs `select = ["ALL"]` with `preview = true`, so suppressions are common and their format matters. Write them as `# ruff:ignore[full-rule-name]` (no space after the colon, full rule name, never the short code). Do **not** write `# noqa: E501`: preview rule RUF105 rewrites `noqa` into the `ruff:ignore` form on the next `ruff check --fix`, so a `noqa` you commit will not survive. The rule name must be exact; a wrong one re-flags the original violation and adds `invalid-rule-code`, which is the quickest way to tell a live suppression from a dead one.
- `[tool.ruff]` sets `fix = true`, so a plain `uv run ruff check` **rewrites files in place**; it is not a read-only inspection. Use `uv run ruff check --no-fix` to look without touching anything. Never run a narrowed `--select` with fixing on: ruff then applies fixes the full rule set would not, and rewrites files you never meant to change.
- Commit subject max **72** chars, enforced by the `committed` hook at the `commit-msg` stage (`committed.toml`). Body line length is not enforced (`line_length = 500`), but wrap bodies at 72 by convention.
- `CHANGELOG.md` is commitizen-managed and regenerated on `cz bump`. Never hand-edit it, including for breaking changes; the release tooling owns it.
- Version source is `constants.py:__version__` (commitizen `version_files`).
- User-facing documentation lives in `docs/` (the Zensical site). `README.md` is intentionally kept light: a short overview that links into the site. Add or expand user docs under `docs/`, not in the README.

## Gotchas

- A bare `EnvConfig()` runs full validation (requires `name` + a destination). The CLI builds `EnvConfig(**cli_values, _env_file=None)` so the environment only fills fields with no flag (tz, AWS creds).
- The container reads `.env`/`.env.secrets`, so running it locally picks up real S3 credentials.
