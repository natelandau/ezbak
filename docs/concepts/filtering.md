---
icon: lucide/filter
---

# Including and excluding files

By default ezbak backs up every regular file under your source paths. Two regex
options narrow that selection, a small set of noise files is always skipped, and
symlinks are never followed.

## Always-excluded files

ezbak never archives these names, regardless of your other settings:

- `.DS_Store`
- `@eaDir`
- `.Trashes`
- `__pycache__`
- `Thumbs.db`
- `IconCache.db`

These are operating-system and tooling artifacts that add noise to a backup and
never need restoring.

!!! note "Symlinks are skipped"

    ezbak does not follow symbolic links. It logs a warning for each one and
    leaves it out of the archive, so a backup never escapes the source tree
    through a link.

## Include and exclude regex

Two options filter the file list by matching against each file's path:

- `include_regex` backs up only files whose path matches the pattern.
- `exclude_regex` skips files whose path matches the pattern.

```python
from pathlib import Path
from ezbak import EZBak, BackupConfig

EZBak(
    BackupConfig(
        name="logs",
        source_paths=[Path("/var/log")],
        storage_paths=[Path("/backups")],
        include_regex=r"\.log$",   # only .log files
        exclude_regex=r"debug",    # skip anything matching "debug"
        keep_last=10,
    )
)
```

On the command line, the same options are `create --include-regex` (`-i`) and
`create --exclude-regex` (`-e`). In the environment they are
`EZBAK_INCLUDE_REGEX` and `EZBAK_EXCLUDE_REGEX`.

!!! info "How include and exclude combine"

    A file is archived when it matches `include_regex` (or no include pattern is
    set) and does not match `exclude_regex`. The always-excluded names above are
    skipped before either pattern runs.

The patterns are standard Python regular expressions, matched against the file
path. `\.log$` matches paths ending in `.log`; `debug` matches any path
containing the substring `debug`.
