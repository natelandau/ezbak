"""Storage backends abstracting local-filesystem and S3 backup operations.

Each backend owns how a single storage kind (local directories or an S3 bucket) indexes, writes, deletes, downloads, and renames backups, so the manager can drive any configured storage uniformly instead of branching on storage type at every call site.
"""

from .aws import AWSService
from .base import StorageBackend
from .local import LocalBackend
from .s3 import S3Backend

__all__ = ["AWSService", "LocalBackend", "S3Backend", "StorageBackend"]
