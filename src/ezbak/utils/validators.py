"""Validators for EZBak."""

from pathlib import Path


def validate_source_paths(source_paths: list[Path] | None) -> None:
    """Validate that at least one source path is configured and every one exists.

    Args:
        source_paths (list[Path] | None): The source paths to validate.

    Raises:
        ValueError: If no source paths are provided or a source path does not exist.
    """
    if not source_paths:
        msg = "No source paths provided"
        raise ValueError(msg)

    for source in source_paths:
        if not source.exists():
            msg = f"Source does not exist: {source}"
            raise ValueError(msg)


def validate_storage_paths(
    storage_paths: list[Path] | None, *, create_if_missing: bool = False
) -> None:
    """Validate that at least one storage path is configured and reachable.

    Args:
        storage_paths (list[Path] | None): The storage paths to validate.
        create_if_missing (bool): Whether to create the storage paths if they do not exist.

    Raises:
        ValueError: If no storage paths are provided or a storage path does not exist and create_if_missing is False.
    """
    if not storage_paths:
        msg = "No storage paths provided"
        raise ValueError(msg)

    for storage_path in storage_paths:
        if not storage_path.exists():
            if create_if_missing:
                storage_path.mkdir(parents=True, exist_ok=True)
            else:
                msg = f"Storage path does not exist: {storage_path}"
                raise ValueError(msg)
