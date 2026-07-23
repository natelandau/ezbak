"""AWS service class for managing S3 bucket operations."""

from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger

from ezbak.exceptions import StorageInitError


def is_missing_object_error(error: ClientError) -> bool:
    """Report whether a ClientError means the requested object does not exist.

    Use to branch "absent, expected" from real failures without a HEAD request
    before every GET. The code differs by operation: HEAD-based calls surface a
    bare "404" while GET surfaces "NoSuchKey".

    Args:
        error (ClientError): The error raised by the S3 call.

    Returns:
        bool: True when the error is a missing-object response.
    """
    return error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}


class AWSService:
    """Manage file operations on Amazon S3 buckets with automatic credential validation."""

    def __init__(
        self,
        aws_access_key: str,
        aws_secret_key: str,
        bucket_name: str,
        bucket_path: str | None = None,
        region: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        """Initialize AWS S3 client with credentials and validate bucket access.

        Set up the S3 client with retry configuration and validate that the bucket exists and is accessible. Use this class when you need to perform file operations on a specific S3 bucket with predefined credentials.

        Args:
            aws_access_key (str): The AWS access key ID.
            aws_secret_key (str): The AWS secret access key.
            bucket_name (str): The target S3 bucket.
            bucket_path (str | None): Key prefix within the bucket. Defaults to None.
            region (str | None): AWS region. None defers to boto3's standard resolution
                (AWS_REGION/AWS_DEFAULT_REGION/~/.aws/config). Defaults to None.
            endpoint_url (str | None): Custom S3 endpoint for S3-compatible storage such
                as MinIO. None uses the AWS default endpoint. Defaults to None.

        Raises:
            StorageInitError: If the credentials are missing or the bucket cannot be accessed.
        """
        logger.debug("AWSService: Initializing")

        self.aws_access_key = aws_access_key
        self.aws_secret_key = aws_secret_key
        self.bucket_path = bucket_path or ""
        self.bucket = bucket_name

        if not all([self.aws_access_key, self.aws_secret_key, self.bucket]):
            msg = "AWS credentials are not set"
            logger.error(msg)
            raise StorageInitError(msg)

        # A blank value (common in .env templates) would reach boto3 as "" and build an
        # invalid endpoint like "https://s3..amazonaws.com"; normalize to None to defer to
        # boto3's standard resolution instead.
        region = region or None
        endpoint_url = endpoint_url or None

        # Construct the client inside the guard: a malformed endpoint (e.g. a missing scheme)
        # makes boto3 raise ValueError at construction, which must surface as a StorageInitError
        # so core.py records a failed storage location instead of escaping as a raw traceback.
        try:
            self.s3 = boto3.client(
                "s3",
                aws_access_key_id=self.aws_access_key,
                aws_secret_access_key=self.aws_secret_key,
                region_name=region,
                endpoint_url=endpoint_url,
                config=Config(retries={"max_attempts": 10, "mode": "standard"}),
            )
            self.location = self.s3.get_bucket_location(Bucket=self.bucket)  # Ex. us-east-1
        except (BotoCoreError, ClientError, ValueError) as e:
            msg = f"Cannot access S3 bucket '{self.bucket}': {e}"
            logger.error(msg)
            raise StorageInitError(msg) from e

    def build_full_key(self, key: str) -> str:
        """Build the full S3 key by prepending bucket_path if needed.

        Args:
            key (str): The S3 object key.

        Returns:
            str: The full S3 key with bucket_path prepended if necessary.
        """
        if not self.bucket_path:
            return key

        normalized_bucket_path = self.bucket_path.rstrip("/") + "/"

        if key.startswith(normalized_bucket_path):
            return key

        return f"{normalized_bucket_path}{key}"

    def delete_object(self, key: str) -> bool:
        """Delete a file from the configured S3 bucket.

        Remove a file from the S3 bucket by its key. Use this method when you need to clean up files from S3 storage or remove outdated backups. The method automatically handles bucket path prefixes and provides detailed logging of the deletion process.

        Args:
            key (str): The S3 object key to delete.

        Returns:
            bool: True if deletion succeeds. Botocore errors from the delete propagate to the caller.
        """
        full_key = self.build_full_key(key)

        logger.trace(f"S3: Attempting to delete {full_key}")
        self.s3.delete_object(Bucket=self.bucket, Key=full_key)

        logger.trace(f"S3: Deleted {key}")
        return True

    def delete_objects(self, keys: list[str]) -> list[str]:
        """Delete multiple files from the configured S3 bucket.

        Remove multiple files from the S3 bucket by their keys using batch deletion. Use this method when you need to efficiently delete multiple files at once, such as cleaning up multiple outdated backups or removing a batch of files. The method automatically handles bucket path prefixes and provides detailed logging of the deletion process.

        Args:
            keys (list[str]): List of S3 object keys to delete.

        Returns:
            list[str]: The full S3 keys that were confirmed deleted (empty if no keys were provided). Botocore errors from the delete propagate to the caller.

        Raises:
            ValueError: If the keys list contains more than 1000 items.
        """
        if not keys:
            logger.warning("S3: No keys provided for deletion")
            return []

        if len(keys) > 1000:  # ruff:ignore[magic-value-comparison]
            msg = "S3: Cannot delete more than 1000 objects at once"
            logger.error(msg)
            raise ValueError(msg)

        objects_to_delete = [{"Key": self.build_full_key(key)} for key in keys]
        logger.trace(f"S3: Attempting to delete {len(objects_to_delete)} objects")

        response = self.s3.delete_objects(
            Bucket=self.bucket,
            Delete={
                "Objects": objects_to_delete,
                "Quiet": False,  # Return info about deleted objects
            },
        )

        # Log successful deletions
        response_deleted_objects = response.get("Deleted", [])
        for obj in response_deleted_objects:
            logger.trace(f"S3: Deleted {obj['Key']}")

        # Handle any errors that occurred during deletion
        errors = response.get("Errors", [])
        if errors:
            for error in errors:
                logger.error(
                    f"S3: Failed to delete '{error['Key']}': {error['Code']} - {error['Message']}"
                )

        logger.trace(f"S3: Successfully deleted {len(response_deleted_objects)} objects")

        return [str(obj["Key"]) for obj in response.get("Deleted", [])]

    def get_object(self, key: str, destination: Path) -> Path:
        """Retrieve the contents of an object from the S3 bucket using boto3's managed transfer.

        Download a file from S3 to a local destination. Use this method when you need to retrieve files from S3 for local processing, backup restoration, or file analysis. The managed transfer downloads large objects with concurrent ranged requests and per-part retries, so a multi-gigabyte restore is not throttled by a single sequential stream. The method automatically handles bucket path prefixes.

        Args:
            key (str): The S3 object key to retrieve.
            destination (Path): The local path to save the object to.

        Returns:
            Path: The destination path where the object was saved. Botocore errors from the download propagate to the caller.
        """
        full_key = self.build_full_key(key)
        logger.trace(f"S3: Attempting to download '{full_key}' to '{destination}'")

        self.s3.download_file(Bucket=self.bucket, Key=full_key, Filename=str(destination))

        logger.trace(f"S3: Downloaded '{full_key}' to '{destination}'")
        return destination

    def get_object_content(self, key: str) -> str:
        """Return a small object's body as text, without staging it on disk.

        Use for tiny objects like checksum sidecars, where a download-to-file
        round trip buys nothing. The method automatically handles bucket path
        prefixes.

        Args:
            key (str): The S3 object key to read.

        Returns:
            str: The object body decoded as UTF-8. Botocore errors from the read and
                UnicodeDecodeError from the decode propagate to the caller.
        """
        full_key = self.build_full_key(key)
        logger.trace(f"S3: Attempting to read '{full_key}'")
        response = self.s3.get_object(Bucket=self.bucket, Key=full_key)
        content = response["Body"].read().decode()
        logger.trace(f"S3: Read '{full_key}' ({len(content)} bytes)")
        return content

    def list_objects(self, prefix: str = "") -> list[str]:
        """List all objects in the configured S3 bucket that start with the specified prefix.

        Discover files in the S3 bucket that match a specific prefix pattern. Use this method when you need to enumerate files for backup management, cleanup operations, or to find specific file patterns. The method automatically handles bucket path prefixes and provides efficient pagination for large buckets.

        Args:
            prefix (str, optional): The prefix to filter object keys by. If empty, return all objects.

        Returns:
            list[str]: A list of S3 object keys that match the specified prefix.
        """
        full_prefix = self.build_full_key(prefix)
        object_keys: list[str] = []

        logger.trace(f"S3: Attempting to list objects with prefix '{full_prefix}'")
        try:
            paginator = self.s3.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket, Prefix=full_prefix)
            for page in pages:
                object_keys.extend(obj["Key"] for obj in page.get("Contents", []))
        except ClientError as e:
            logger.error(f"Failed to list objects with prefix '{prefix}': {e}")
            return []

        logger.trace(f"S3: Listed {len(object_keys)} objects with prefix '{full_prefix}'")
        return object_keys

    def upload_content(self, *, content: str, name: str) -> bool:
        """Upload small in-memory content as an object, without staging it on disk.

        Use for tiny generated objects like checksum sidecars, where a write-to-disk
        round trip before the upload buys nothing.

        Args:
            content (str): The object body, stored UTF-8 encoded.
            name (str): The desired object key.

        Returns:
            bool: True if upload succeeds. Botocore errors from the upload propagate to the caller.
        """
        full_name = self.build_full_key(name)
        self.s3.put_object(Bucket=self.bucket, Key=full_name, Body=content.encode())
        logger.trace(f"S3: Uploaded '{name}' to '{full_name}'")
        return True

    def upload_object(self, file: Path, name: str) -> bool:
        """Upload a local file to the configured S3 bucket.

        Store a file from the local filesystem to the S3 bucket using the configured bucket path. Use this method when you need to store files in S3 for backup, sharing, or cloud storage purposes. The method automatically handles the bucket path prefix and provides detailed logging.

        Args:
            file (Path): The local file path to upload to S3.
            name (str): The desired name for the file in S3.

        Returns:
            bool: True if upload succeeds. Transfer errors from the upload (including
                boto3's S3UploadFailedError wrapping) propagate to the caller.
        """
        full_name = self.build_full_key(name)

        self.s3.upload_file(Filename=file, Bucket=self.bucket, Key=full_name)

        logger.trace(f"S3: Uploaded '{name}' to '{full_name}'")
        return True
