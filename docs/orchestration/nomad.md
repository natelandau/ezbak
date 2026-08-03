---
icon: lucide/server
---

# Nomad example

This jobspec runs a service alongside the three ezbak tasks. The pre-start task
restores the latest backup before the service starts. The sidecar backs up on a
schedule while the service runs. The post-stop task takes a final backup as the
allocation stops.

## The jobspec

The three ezbak tasks and the service share one `data` volume. The task lifecycle
hooks of Nomad decide when each ezbak task runs.

```hcl title="service.nomad.hcl"
job "my-service" {
  group "app" {
    volume "data" {
      type   = "host"
      source = "my-service-data"
    }

    # Pre-start: restore the latest backup before the service starts. (1)
    task "restore" {
      lifecycle {
        hook    = "prestart"
        sidecar = false
      }
      driver = "docker"
      config {
        image = "ghcr.io/natelandau/ezbak:latest"
      }
      volume_mount {
        volume      = "data"
        destination = "/data"
      }
      env {
        EZBAK_ACTION              = "restore"
        EZBAK_NAME                = "my-service"
        EZBAK_AWS_S3_BUCKET_NAME  = "my-backups"
        EZBAK_RESTORE_PATH        = "/data"
        EZBAK_SKIP_IF_NO_BACKUP   = "true" # (2)!
      }
    }

    # Sidecar: back up on a schedule while the service runs. (3)
    task "backup" {
      lifecycle {
        hook    = "poststart"
        sidecar = true
      }
      driver = "docker"
      config {
        image = "ghcr.io/natelandau/ezbak:latest"
      }
      volume_mount {
        volume      = "data"
        destination = "/data"
        read_only   = true
      }
      env {
        EZBAK_ACTION              = "backup"
        EZBAK_NAME                = "my-service"
        EZBAK_SOURCE_PATHS        = "/data"
        EZBAK_AWS_S3_BUCKET_NAME  = "my-backups"
        EZBAK_CRON                = "0 * * * *" # (4)!
        EZBAK_KEEP_HOURLY         = "24"
        EZBAK_KEEP_DAILY          = "7"
        EZBAK_HEALTHCHECK_URL     = "https://hc-ping.com/your-uuid"
        TZ                        = "America/New_York"
      }
    }

    # Post-stop: one final backup as the allocation stops. (5)
    task "final-backup" {
      lifecycle {
        hook    = "poststop"
        sidecar = false
      }
      driver = "docker"
      config {
        image = "ghcr.io/natelandau/ezbak:latest"
      }
      volume_mount {
        volume      = "data"
        destination = "/data"
        read_only   = true
      }
      env {
        EZBAK_ACTION              = "backup"
        EZBAK_NAME                = "my-service"
        EZBAK_SOURCE_PATHS        = "/data"
        EZBAK_AWS_S3_BUCKET_NAME  = "my-backups"
        EZBAK_KEEP_HOURLY         = "24"
        EZBAK_KEEP_DAILY          = "7"
      }
    }

    task "my-service" {
      driver = "docker"
      config {
        image = "my-service:latest"
      }
      volume_mount {
        volume      = "data"
        destination = "/data"
      }
    }
  }
}
```

1.  `hook = "prestart"` with `sidecar = false` runs this task to completion
    before the main task starts, so the data is in place first.
2.  On a fresh deployment there is no backup yet. `EZBAK_SKIP_IF_NO_BACKUP` makes
    a missing backup a clean no-op, so the job can still start. See [Fresh
    deploys](fresh-deploys.md). It does not cover a storage location ezbak cannot
    read. That still fails the task and blocks the job from starting, because a
    real backup can exist there. See [An unreadable storage location is not an
    empty
    one](../concepts/failure-behavior.md#an-unreadable-storage-location-is-not-an-empty-one).
3.  `hook = "poststart"` with `sidecar = true` keeps this task running alongside
    the service. `EZBAK_CRON` keeps the container up and backing up on schedule.
4.  This cron runs hourly. A scheduled backup prunes afterward with the retention
    options, so old backups do not accumulate.
5.  `hook = "poststop"` runs this task after the main task stops. It captures the
    final state before Nomad clears the allocation.

## How the pieces fit together

The three ezbak tasks share two things with the service: the `data` volume and
the `EZBAK_NAME`. The name groups the backup set, and the shared bucket makes the
backups reachable from any host the job lands on.

- The **restore** task mounts `data` writable and stages the latest backup into
  it.
- The **backup** sidecar and the **final-backup** task mount `data` read-only, so
  they never modify the live data of the service.
- All three point at the same `EZBAK_AWS_S3_BUCKET_NAME` and `EZBAK_NAME`.

!!! warning "EZBAK_SQLITE_PATHS needs a writable mount"

    When you snapshot databases, delete `read_only = true` from the
    `volume_mount` of the backup tasks. `EZBAK_SQLITE_PATHS` is the one exception
    to the read-only mounts above, because SQLite can have to create a `-shm`
    file to read a WAL database. See [SQLite
    databases](../concepts/sqlite.md#mount-the-source-read-write).

!!! warning "A shutdown backup races the kill timeout"

    Set `EZBAK_BACKUP_ON_SHUTDOWN = "true"` on the backup sidecar to back up once
    more when Nomad stops it. Nomad holds the allocation alive only for the
    `kill_timeout` of the task, so raise that value to cover the backup:

    ```hcl
    kill_timeout = "5m"
    ```

    If the backup outlasts `kill_timeout`, Nomad force-kills the task and the
    backup is lost. The `poststop` task above runs as its own step, with its own
    window. It is therefore the more reliable choice for backups that can run
    long.

!!! tip "Keep credentials out of the jobspec"

    The example holds a bucket name inline for clarity. In practice, read
    `EZBAK_AWS_ACCESS_KEY` and `EZBAK_AWS_SECRET_KEY` from the Vault integration
    of Nomad, or from a secrets store, not from a committed jobspec.

    On a Nomad client that runs on EC2, an instance profile removes the key pair
    entirely. Attach an IAM role to the instance, then delete
    `EZBAK_AWS_ACCESS_KEY` and `EZBAK_AWS_SECRET_KEY` from the `env` of every
    task. ezbak then authenticates as that role. The role needs `s3:ListBucket`
    on the bucket, plus `s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` on
    its contents. See [Instance roles and ambient
    credentials](../guides/s3.md#instance-roles-and-ambient-credentials).

## Forcing an on-demand backup

Signal the sidecar to back up immediately, ahead of its cron schedule:

```bash
nomad alloc signal -s SIGUSR1 -task backup <alloc>
```

For what the signal does and how it behaves, see
[Forcing an on-demand backup](../guides/docker.md#forcing-an-on-demand-backup).

For the same pattern on Kubernetes, see the [Kubernetes example](kubernetes.md).
