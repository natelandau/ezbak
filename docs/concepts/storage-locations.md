---
icon: lucide/database
---

# Storage locations

ezbak sends each backup to the storage locations you configure. There is no
storage-type option to pick. The locations you configure decide where backups go.

## Local, S3, or both

You configure storage by naming the locations, and the backends follow.

- Set `storage_paths` to back up to one or more local directories.
- Set `aws_s3_bucket_name` to back up to S3. Add `aws_access_key` and
  `aws_secret_key` to authenticate explicitly. Omit both to use the credentials
  of the host, such as an EC2 instance profile or an EKS service account.
- Set both to write every backup to local storage and to S3 at the same time.

```mermaid
graph LR
  E["EZBak"] --> L1["local: /backups"]
  E --> L2["local: /mnt/nas/backups"]
  E --> S3["S3: my-bucket"]
```

At least one storage location is required. A `BackupConfig` with neither
`storage_paths` nor `aws_s3_bucket_name` fails validation.

## Every location gets every backup

A backup run writes the same archive to each configured location. Two local
directories plus a bucket means three copies of each backup. This is how the
[orchestration pattern](../orchestration/index.md) works. It keeps a local copy
on the host, and a shared copy in S3 that follows the job to another host.

## Symlinks in a local storage directory

ezbak never follows a symlink in a local storage directory. Other jobs or hosts
that can write to the directory cannot use a link to make ezbak read or change a
file outside it.

- ezbak skips a file with a backup name that is a symlink and logs a warning.
  The file is not in the backup list, and ezbak never restores or prunes it.
- Each backup and [checksum file](checksums.md) is a new regular file. If a link
  has the same name, ezbak replaces the link and does not write to its target.
- If an archive becomes a symlink before a restore reads it, the restore fails.
- If a checksum file is a symlink or a special file such as a FIFO, ezbak
  ignores it and logs a warning. The restore continues without checksum
  verification, the same as for a missing checksum file.

## When a location cannot be used

A configured location can fail for three reasons: bad S3 credentials, an
unreachable bucket, or a local directory ezbak cannot create. In each case the
run fails instead of reporting success. ezbak still writes to every location that
works, so a partial failure keeps the copies that succeeded.

[Failure behavior](failure-behavior.md) describes how that failure surfaces on
each interface and which errors it raises. For the S3 setup, see
[Back up to S3](../guides/s3.md).
