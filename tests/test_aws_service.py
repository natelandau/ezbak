"""Direct AWSService tests backed by moto's in-memory S3."""

import boto3


def test_s3_bucket_fixture_smoke(s3_bucket: str) -> None:
    """Verify the s3_bucket fixture yields a bucket that exists in moto."""
    # Given: the moto-backed bucket fixture
    # When: listing buckets through a fresh client inside the mock context
    client = boto3.client("s3", region_name="us-east-1")
    names = [b["Name"] for b in client.list_buckets()["Buckets"]]
    # Then: the fixture's bucket exists
    assert s3_bucket in names
