"""AWS service class for managing S3 bucket operations."""

import sys
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from nclutils import logger

from ezbak.models import settings


class AWSService:
    """Manage file operations on Amazon S3 buckets with automatic credential validation."""

    def __init__(self) -> None:
        """Initialize AWS S3 client with credentials and validate bucket access.

        Set up the S3 client with retry configuration and validate that the bucket exists and is accessible. Use this class when you need to perform file operations on a specific S3 bucket with predefined credentials.

        Raises:
            ValueError: If the AWS credentials are not set.
        """
        logger.debug("AWSService: Initializing")

        self.aws_access_key = settings.aws_access_key
        self.aws_secret_key = settings.aws_secret_key
        self.bucket_path = settings.aws_s3_bucket_path or ""
        self.bucket = settings.aws_s3_bucket_name

        if not all([self.aws_access_key, self.aws_secret_key, self.bucket]):
            msg = "AWS credentials are not set"
            logger.error(msg)
            raise ValueError(msg)

        self.s3 = boto3.client(
            "s3",
            aws_access_key_id=self.aws_access_key,
            aws_secret_access_key=self.aws_secret_key,
            config=Config(retries={"max_attempts": 10, "mode": "standard"}),
        )

        try:
            self.location = self.s3.get_bucket_location(Bucket=self.bucket)  # Ex. us-east-1
        except ClientError as e:
            logger.error(e)
            sys.exit(1)

    def _build_full_key(self, key: str) -> str:
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

    def object_exists(self, key: str) -> bool:
        """Check if a file exists in the S3 bucket.

        Verify the existence of a file in S3 before performing operations on it. Use this method when you need to check if a file exists before attempting to download, delete, or modify it. The method automatically handles bucket path prefixes and provides detailed logging of the existence check.

        Args:
            key (str): The S3 object key to check.

        Returns:
            bool: True if the file exists, False if it does not.

        Raises:
            ClientError: If the file cannot be checked.
        """
        full_key = self._build_full_key(key)
        try:
            self.s3.head_object(Bucket=self.bucket, Key=full_key)
            logger.trace(f"S3 file exists: '{full_key}'")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                logger.debug(f"S3: File '{full_key}' does not exist (404 Not Found).")
                return False
            logger.error(f"S3: Error checking existence of '{full_key}': {e}")
            raise

        return True

    def delete_object(self, key: str) -> bool:
        """Delete a file from the configured S3 bucket.

        Remove a file from the S3 bucket by its key. Use this method when you need to clean up files from S3 storage or remove outdated backups. The method automatically handles bucket path prefixes and provides detailed logging of the deletion process.

        Args:
            key (str): The S3 object key to delete.

        Returns:
            bool: True if deletion succeeds, False if any error occurs during deletion.

        Raises:
            ClientError: If the file cannot be deleted.
        """
        full_key = self._build_full_key(key)

        logger.trace(f"S3: Attempting to delete {full_key}")
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=full_key)
        except ClientError as e:
            logger.error(e)
            raise

        logger.trace(f"S3: Deleted {key}")
        return True

    def delete_objects(self, keys: list[str]) -> int:
        """Delete multiple files from the configured S3 bucket.

        Remove multiple files from the S3 bucket by their keys using batch deletion. Use this method when you need to efficiently delete multiple files at once, such as cleaning up multiple outdated backups or removing a batch of files. The method automatically handles bucket path prefixes and provides detailed logging of the deletion process.

        Args:
            keys (list[str]): List of S3 object keys to delete.

        Returns:
            bool: True if all deletions succeed, False if any error occurs during deletion.

        Raises:
            ClientError: If the files cannot be deleted.
            ValueError: If the keys list is empty or contains more than 1000 items.
        """
        if not keys:
            logger.warning("S3: No keys provided for deletion")
            return 0

        if len(keys) > 1000:  # noqa: PLR2004
            msg = "S3: Cannot delete more than 1000 objects at once"
            logger.error(msg)
            raise ValueError(msg)

        objects_to_delete = [{"Key": self._build_full_key(key)} for key in keys]
        logger.trace(f"S3: Attempting to delete {len(objects_to_delete)} objects")

        try:
            response = self.s3.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": objects_to_delete,
                    "Quiet": False,  # Return info about deleted objects
                },
            )

            # Log successful deletions
            deleted_objects = response.get("Deleted", [])
            for obj in deleted_objects:
                logger.trace(f"S3: Deleted {obj['Key']}")

            # Handle any errors that occurred during deletion
            errors = response.get("Errors", [])
            if errors:
                for error in errors:
                    logger.error(
                        f"S3: Failed to delete '{error['Key']}': {error['Code']} - {error['Message']}"
                    )
                return len(deleted_objects)

            logger.trace(f"S3: Successfully deleted {len(deleted_objects)} objects")

        except ClientError as e:
            logger.error(f"S3: Failed to delete objects: {e}")
            raise

        return True

    def get_object(self, key: str, destination: Path) -> Path:
        """Retrieve the contents of an object from the S3 bucket using streaming.

        Download a file from S3 to a local destination with efficient streaming. Use this method when you need to retrieve files from S3 for local processing, backup restoration, or file analysis. The method uses streaming to handle large files efficiently and automatically handles bucket path prefixes.

        Args:
            key (str): The S3 object key to retrieve.
            destination (Path): The local path to save the object to.

        Returns:
            Path: The destination path where the object was saved.

        Raises:
            ClientError: If the object cannot be downloaded.
        """
        full_key = self._build_full_key(key)
        logger.trace(f"S3: Attempting to download '{full_key}' to '{destination}'")
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=full_key)

            with destination.open("wb") as f:
                for chunk in response["Body"].iter_chunks(chunk_size=8192):
                    f.write(chunk)
        except ClientError as e:
            logger.error(f"S3: Failed to download {key}: {e}")
            raise

        logger.trace(f"S3: Downloaded '{full_key}' to '{destination}'")
        return destination

    def list_objects(self, prefix: str = "") -> list[str]:
        """List all objects in the configured S3 bucket that start with the specified prefix.

        Discover files in the S3 bucket that match a specific prefix pattern. Use this method when you need to enumerate files for backup management, cleanup operations, or to find specific file patterns. The method automatically handles bucket path prefixes and provides efficient pagination for large buckets.

        Args:
            prefix (str, optional): The prefix to filter object keys by. If empty, return all objects.

        Returns:
            list[str]: A list of S3 object keys that match the specified prefix.
        """
        full_prefix = self._build_full_key(prefix)
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

    def rename_object(self, current_name: str, new_name: str) -> bool:
        """Rename a file in the configured S3 bucket by copying and then deleting the old one.

        Change the name of a file in S3 while preserving its content and metadata. Use this method when you need to reorganize files in S3, implement versioning schemes, or correct file naming conventions. The method performs a copy operation followed by deletion to ensure data integrity.

        Args:
            current_name (str): The current name of the file.
            new_name (str): The new name of the file.

        Returns:
            bool: True if renaming succeeds, False if any error occurs during renaming.

        Raises:
            ClientError: If the file cannot be renamed.
        """
        full_current_name = self._build_full_key(current_name)
        full_new_name = self._build_full_key(new_name)

        logger.trace(f"S3: Attempting to rename '{full_current_name}' to '{full_new_name}'")
        try:
            copy_source = {"Bucket": self.bucket, "Key": full_current_name}
            self.s3.copy_object(Bucket=self.bucket, CopySource=copy_source, Key=full_new_name)
            logger.trace(f"S3: Copied '{full_current_name}' to '{full_new_name}'.")

        except ClientError as e:
            logger.error(f"S3: Failed to rename '{current_name}' to '{new_name}': {e}")
            raise

        if not self.object_exists(full_new_name):
            raise ClientError(
                {
                    "Error": {
                        "Code": "FailedCopyVerification",
                        "Message": "Copied object not found after copy operation.",
                    }
                },
                "HeadObject",
            )

        try:
            self.s3.delete_object(Bucket=self.bucket, Key=full_current_name)

        except ClientError as e:
            logger.error(f"S3: Failed to rename '{current_name}' to '{new_name}': {e}")
            raise

        logger.trace(f"S3: Renamed '{current_name}' to '{new_name}'.")
        return True

    def upload_object(self, file: Path, name: str = "") -> bool:
        """Upload a local file to the configured S3 bucket.

        Store a file from the local filesystem to the S3 bucket using the configured bucket path. Use this method when you need to store files in S3 for backup, sharing, or cloud storage purposes. The method automatically handles the bucket path prefix and provides detailed logging.

        Args:
            file (Path): The local file path to upload to S3.
            name (str, optional): The desired name for the file in S3. If not provided, use the original filename.

        Returns:
            bool: True if upload succeeds, False if any error occurs during upload.

        Raises:
            ClientError: If the file cannot be uploaded.
        """
        if not name:
            name = file.name

        full_name = self._build_full_key(name)

        try:
            self.s3.upload_object(file, self.bucket, full_name)
        except ClientError as e:
            logger.error(e)
            raise

        logger.trace(f"S3: Uploaded '{name}' to '{full_name}'")
        return True
