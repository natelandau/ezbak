[![Tests](https://github.com/natelandau/ezbak/actions/workflows/test.yml/badge.svg)](https://github.com/natelandau/ezbak/actions/workflows/test.yml) [![codecov](https://codecov.io/gh/natelandau/ezbak/graph/badge.svg?token=lR581iFOIE)](https://codecov.io/gh/natelandau/ezbak)

# ezbak

ezbak moves shared state between jobs and hosts in an orchestrated environment like Nomad or Kubernetes. It creates, prunes, and restores compressed archives on the local filesystem, in AWS S3, or both. The Docker container is the main way to run it, with a Python package and a command-line tool for scripting and one-off use.

ezbak is a small, focused backup manager. It does not aim to replace restic, borg, or a full backup system.

## Documentation

Full documentation lives at **[natelandau.github.io/ezbak](https://natelandau.github.io/ezbak/)**:

- [Quickstart](https://natelandau.github.io/ezbak/getting-started/quickstart/): a first backup in five minutes
- [The orchestration pattern](https://natelandau.github.io/ezbak/orchestration/): backups that follow a job across hosts, with Nomad and Kubernetes examples
- [Configuration reference](https://natelandau.github.io/ezbak/reference/configuration/): every option across the library, CLI, and environment

## Install

```bash
uv add ezbak            # Python package
uv tool install ezbak   # command-line tool
docker pull ghcr.io/natelandau/ezbak:latest   # container
```

ezbak requires Python 3.11 or higher.

## Quickstart

```python
from ezbak import ezbak

backups = ezbak(name="my-backup", source_paths=["/data"], storage_paths=["/backups"], max_backups=7)
backups.create_backup()
backups.restore_backup(restore_path="/restore")
```

The command line and container do the same work. See the [quickstart](https://natelandau.github.io/ezbak/getting-started/quickstart/) for all three.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
