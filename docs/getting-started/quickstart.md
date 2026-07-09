---
icon: lucide/rocket
---

# Quickstart

This is the shortest path to a first backup. Pick the interface you installed,
make a backup, and restore it.

## Make a backup

=== "Container"

    ```bash
    docker run -it \
        -v /path/to/source:/source:ro \
        -v /path/to/backups:/backups \
        -e EZBAK_ACTION=backup \
        -e EZBAK_NAME=my-backup \
        -e EZBAK_SOURCE_PATHS=/source \
        -e EZBAK_STORAGE_PATHS=/backups \
        -e EZBAK_KEEP_LAST=7 \
        ghcr.io/natelandau/ezbak:latest
    ```

=== "CLI"

    ```bash
    ezbak --name my-backup --storage ~/Backups create --source ~/Documents
    ```

=== "Python"

    ```python
    from ezbak import ezbak

    backups = ezbak(
        name="my-backup",
        source_paths=["/data"],
        storage_paths=["/backups"],
    )
    backups.create_backup()
    ```

A backup file appears in your storage location, named
`my-backup-20241215T143022.tgz`. See [Backup names](../concepts/backup-names.md)
for the format.

## List what you have

=== "CLI"

    ```bash
    ezbak --name my-backup --storage ~/Backups list
    ```

=== "Python"

    ```python
    print([backup.name for backup in backups.list_backups()])
    ```

## Restore it

=== "Container"

    ```bash
    docker run -it \
        -v /path/to/backups:/backups:ro \
        -v /path/to/restore:/restore \
        -e EZBAK_ACTION=restore \
        -e EZBAK_NAME=my-backup \
        -e EZBAK_STORAGE_PATHS=/backups \
        -e EZBAK_RESTORE_PATH=/restore \
        ghcr.io/natelandau/ezbak:latest
    ```

=== "CLI"

    ```bash
    ezbak --name my-backup --storage ~/Backups restore --restore-path ~/restore
    ```

=== "Python"

    ```python
    backups.restore_backup(restore_path="/restore")
    ```

That is a full cycle: create, list, restore.

## Where to go next

<div class="grid cards" markdown>

-   :material-cog-transfer: __Run it in orchestration__

    ---

    The workflow ezbak is built for: backups that follow a job across hosts.

    [:octicons-arrow-right-24: The orchestration pattern](../orchestration/index.md)

-   :material-tune: __Configure retention__

    ---

    Combine a fixed count with time-based rules that compose.

    [:octicons-arrow-right-24: Retention policies](../concepts/retention.md)

-   :material-cloud-upload-outline: __Back up to S3__

    ---

    Write to a bucket, or to local storage and S3 at once.

    [:octicons-arrow-right-24: Back up to S3](../guides/s3.md)

-   :material-book-open-variant: __See every option__

    ---

    The full option list across the library, CLI, and environment.

    [:octicons-arrow-right-24: Configuration reference](../reference/configuration.md)

</div>
