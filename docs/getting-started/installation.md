---
icon: lucide/download
---

# Installation

ezbak ships as three things from one codebase: a Docker container, a Python
package, and a command-line tool. Install the one that matches how you plan to
run it. The container is the primary interface. The package and the CLI are for
scripting, local tests, and one-off backups.

The package and the CLI require Python 3.11.4 or higher. The container bundles
its own runtime.

## Docker container

Pull the image from the GitHub Container Registry. This is the interface to use
in an orchestrated deployment.

```bash
docker pull ghcr.io/natelandau/ezbak:latest
```

The container reads its configuration from environment variables. To run it, see
[Running in Docker](../guides/docker.md).

## Python package

Install the package to drive ezbak from your own code.

=== "uv"

    ```bash
    uv add ezbak
    ```

=== "pip"

    ```bash
    pip install ezbak
    ```

See [Using the Python library](../guides/python.md).

## Command-line tool

Install the CLI on its own to run backups from a shell.

=== "uv"

    ```bash
    uv tool install ezbak
    ```

=== "pip"

    ```bash
    python -m pip install --user ezbak
    ```

Make sure that the CLI runs:

```bash
ezbak --help
```

See [Using the CLI](../guides/cli.md).

## Next step

Read the [quickstart](quickstart.md) for the shortest path to a first backup.
