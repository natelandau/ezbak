---
icon: lucide/filter
---

# Including and excluding files

By default ezbak backs up every regular file under your source paths. Two
regular expressions narrow that selection. A small set of noise files is always
skipped, and ezbak never follows a symlink.

## Always-excluded files

ezbak never archives these names, whatever else you configure:

- `.DS_Store`
- `@eaDir`
- `.Trashes`
- `__pycache__`
- `Thumbs.db`
- `IconCache.db`

These are operating-system and tooling artifacts. They add noise to a backup, and
you never need to restore them.

!!! note "Symlinks are skipped"

    ezbak does not follow a symbolic link. It logs a warning for each one and
    skips it, so a backup never escapes the source tree through a link.

## Include and exclude regular expressions

Two options filter the file list. ezbak matches each one against the path of the
file:

- `include_regex` backs up only the files whose path matches the pattern.
- `exclude_regex` skips the files whose path matches the pattern.

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

    ezbak archives a file when it matches `include_regex` (or you set no include
    pattern) and does not match `exclude_regex`. ezbak skips the always-excluded
    names above before either pattern runs.

Both options take a standard Python regular expression, matched against the file
path. `\.log$` matches a path that ends in `.log`. `debug` matches any path that
contains the substring `debug`.
