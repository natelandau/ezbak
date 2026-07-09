# Nomad example

This jobspec runs a service alongside the three ezbak tasks. The pre-start task
restores the latest backup before the service starts, the sidecar backs up on a
schedule while it runs, and the post-stop task takes a final backup as the
allocation stops.

## The jobspec

The three ezbak tasks and the service share one `data` volume. Nomad task
lifecycle hooks decide when each ezbak task runs.

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
        EZBAK_RESTORE_IF_EXISTS   = "true" # (2)!
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
        EZBAK_RETENTION_HOURLY    = "24"
        EZBAK_RETENTION_DAILY     = "7"
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
        EZBAK_RETENTION_HOURLY    = "24"
        EZBAK_RETENTION_DAILY     = "7"
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

1.  `hook = "prestart"` with `sidecar = false` runs this task to completion before
    the main task starts, so the data is in place first.
2.  On a fresh deployment there is no backup yet. `EZBAK_RESTORE_IF_EXISTS`
    makes a missing backup a clean no-op so the job can still start. See [Fresh
    deploys](fresh-deploys.md).
3.  `hook = "poststart"` with `sidecar = true` keeps this task running alongside
    the service. `EZBAK_CRON` keeps the container up and backing up on schedule.
4.  This cron runs hourly. A scheduled backup prunes afterward using the
    retention options, so old backups do not build up.
5.  `hook = "poststop"` runs this task after the main task stops, capturing the
    final state before the allocation is cleared.

## How the pieces line up

The three ezbak tasks share two things with the service: the `data` volume and the
`EZBAK_NAME`. The name groups the backup set, and the shared bucket makes the
backups reachable from any host the job lands on.

- The **restore** task mounts `data` writable and stages the latest backup into
  it.
- The **backup** sidecar and **final-backup** task mount `data` read-only, so they
  never modify the service's live data.
- All three point at the same `EZBAK_AWS_S3_BUCKET_NAME` and `EZBAK_NAME`.

!!! warning "A shutdown backup races the kill timeout"

    Set `EZBAK_BACKUP_ON_SHUTDOWN = "true"` on the backup sidecar to back up once
    more when Nomad stops it. Nomad holds the allocation alive only for the task's
    `kill_timeout`, so raise it to cover the backup:

    ```hcl
    kill_timeout = "5m"
    ```

    If the backup outlasts `kill_timeout`, Nomad force-kills the task and the
    backup is lost. The `poststop` task above runs as its own step with its own
    window, so it is the more reliable choice for backups that can run long.

!!! tip "Keep credentials out of the jobspec"

    The example shows a bucket name inline for clarity. In practice, source
    `EZBAK_AWS_ACCESS_KEY` and `EZBAK_AWS_SECRET_KEY` from Nomad's Vault
    integration or a secrets store, not from a committed jobspec.

For the same pattern on Kubernetes, see the [Kubernetes example](kubernetes.md).
