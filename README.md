[![Tests](https://github.com/natelandau/ezbak/actions/workflows/test.yml/badge.svg)](https://github.com/natelandau/ezbak/actions/workflows/test.yml) [![codecov](https://codecov.io/gh/natelandau/ezbak/graph/badge.svg?token=lR581iFOIE)](https://codecov.io/gh/natelandau/ezbak)

# ezbak

ezbak is a backup manager. It creates, prunes, and restores compressed archives of files and directories on the local filesystem, in AWS S3, or both, with regex file filtering, count-based and time-based retention, and point-in-time restore. It was written primarily to move shared state between jobs and hosts in an orchestrated environment like Nomad or Kubernetes. In that setting the Docker container is the main way to run it; a Python package and a command-line tool run the same backups from your own code or a shell.

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

backups = ezbak(name="my-backup", source_paths=["/data"], storage_paths=["/backups"], keep_last=7)
backups.create_backup()
backups.restore_backup(restore_path="/restore")
```

The command line and container do the same work. See the [quickstart](https://natelandau.github.io/ezbak/getting-started/quickstart/) for all three.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
