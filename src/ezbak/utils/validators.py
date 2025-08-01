"""Validators for EZBak."""

from ezbak.models.settings import Settings


def validate_source_paths(settings: Settings) -> None:
    """Validate the source paths.

    Args:
        settings (Settings): The settings to validate.

    Raises:
        ValueError: If the source paths are invalid.
    """
    if not settings.source_paths:
        msg = "No source paths provided"
        raise ValueError(msg)

    for source in settings.source_paths:
        if not source.exists():
            msg = f"Source does not exist: {source}"
            raise ValueError(msg)


def validate_storage_paths(settings: Settings, *, create_if_missing: bool = False) -> None:
    """Validate the storage paths.

    Args:
        settings (Settings): The settings to validate.
        create_if_missing (bool): Whether to create the storage paths if they do not exist.

    Raises:
        ValueError: If the storage paths are invalid.
    """
    if not settings.storage_paths:
        msg = "No storage paths provided"
        raise ValueError(msg)

    for storage_path in settings.storage_paths:
        if not storage_path.exists():
            if create_if_missing:
                storage_path.mkdir(parents=True, exist_ok=True)
            else:
                msg = f"Storage path does not exist: {storage_path}"
                raise ValueError(msg)
