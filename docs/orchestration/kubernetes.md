---
icon: lucide/ship-wheel
---

# Kubernetes example

The three-task pattern maps cleanly onto a Kubernetes pod. An init container
restores the latest backup before the app starts, a native sidecar backs up on a
schedule while it runs, and a `preStop` hook on that sidecar takes a final backup
as the pod terminates.

## The manifest

The restore init container, the backup sidecar, and the app container share one
`data` volume. All three ezbak roles use the same image, name, and bucket.

```yaml title="deployment.yaml"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-service
spec:
  replicas: 1
  selector:
    matchLabels:
      app: my-service
  template:
    metadata:
      labels:
        app: my-service
    spec:
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: my-service-data
      initContainers:
        # Pre-start: restore the latest backup, then exit. (1)
        - name: restore
          image: ghcr.io/natelandau/ezbak:latest
          envFrom:
            - secretRef:
                name: ezbak-aws
          env:
            - name: EZBAK_ACTION
              value: "restore"
            - name: EZBAK_NAME
              value: "my-service"
            - name: EZBAK_AWS_S3_BUCKET_NAME
              value: "my-backups"
            - name: EZBAK_RESTORE_PATH
              value: "/data"
            - name: EZBAK_SKIP_IF_NO_BACKUP
              value: "true" # (2)!
          volumeMounts:
            - name: data
              mountPath: /data

        # Sidecar: back up on a schedule while the app runs. (3)
        - name: backup
          image: ghcr.io/natelandau/ezbak:latest
          restartPolicy: Always
          envFrom:
            - secretRef:
                name: ezbak-aws
          env:
            - name: EZBAK_ACTION
              value: "backup"
            - name: EZBAK_NAME
              value: "my-service"
            - name: EZBAK_SOURCE_PATHS
              value: "/data"
            - name: EZBAK_AWS_S3_BUCKET_NAME
              value: "my-backups"
            - name: EZBAK_CRON
              value: "0 * * * *"
            - name: EZBAK_KEEP_HOURLY
              value: "24"
            - name: EZBAK_KEEP_DAILY
              value: "7"
            - name: TZ
              value: "America/New_York"
          volumeMounts:
            - name: data
              mountPath: /data
              readOnly: true
          lifecycle:
            preStop:
              exec:
                # Post-stop: one final backup before the pod terminates. (4)
                command:
                  - ezbak
                  - --name=my-service
                  - --s3-bucket=my-backups
                  - create
                  - --source=/data

      containers:
        - name: my-service
          image: my-service:latest
          volumeMounts:
            - name: data
              mountPath: /data
```

1.  An init container runs to completion before the app container starts, so the
    restored data is in place first.
2.  On a fresh deployment there is no backup yet. `EZBAK_SKIP_IF_NO_BACKUP` makes
    a missing backup a clean no-op so the pod can still start. See [Fresh
    deploys](fresh-deploys.md). It does not cover a destination ezbak cannot
    read: that still fails the init container and blocks the pod from
    starting, since a real backup might exist there. See [An unreadable
    destination is not an empty
    one](../concepts/failure-behavior.md#an-unreadable-destination-is-not-an-empty-one).
3.  `restartPolicy: Always` on an init container makes it a native sidecar
    (Kubernetes 1.29 and later): it starts before the app container and keeps
    running alongside it. `EZBAK_CRON` keeps it backing up on schedule.
4.  The `preStop` hook runs the ezbak CLI inside the sidecar to take a final
    backup before the pod stops. The sidecar's environment supplies the same
    credentials.

## The credentials secret

The `envFrom` blocks read the AWS credentials from a Secret, so they stay out of
the manifest:

```yaml title="ezbak-aws-secret.yaml"
apiVersion: v1
kind: Secret
metadata:
  name: ezbak-aws
type: Opaque
stringData:
  EZBAK_AWS_ACCESS_KEY: "your-access-key"
  EZBAK_AWS_SECRET_KEY: "your-secret-key"
```

!!! tip "On EKS, use IRSA instead of a Secret"

    IAM roles for service accounts (IRSA) let a pod assume an IAM role directly, with no
    key pair to store or rotate. Annotate the service account with the role's ARN:

    ```yaml title="service-account.yaml"
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: ezbak
      annotations:
        eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/my-service-ezbak
    ```

    Reference it from the pod spec with `serviceAccountName: ezbak`, then drop
    `EZBAK_AWS_ACCESS_KEY` and `EZBAK_AWS_SECRET_KEY` from every task's `env`, and drop the
    `envFrom` block that reads the `ezbak-aws` Secret entirely. The role needs
    `s3:ListBucket` on the bucket plus `s3:GetObject`, `s3:PutObject`, and
    `s3:DeleteObject` on its contents. See [Instance roles and ambient
    credentials](../guides/s3.md#instance-roles-and-ambient-credentials).

## How the pieces line up

The restore init container mounts `data` writable and stages the latest backup.
The backup sidecar mounts `data` read-only, so it never modifies the app's live
data, and its `preStop` hook captures the final state. All three share the
`EZBAK_NAME` and bucket, so the backups follow the pod to any node.

!!! warning "EZBAK_SQLITE_PATHS needs a writable mount"

    Setting `EZBAK_SQLITE_PATHS` is the one exception to the read-only mount
    above. SQLite may have to create a `-shm` file to read a WAL database, so set
    `readOnly: false` on the backup sidecar's `volumeMounts` entry when you
    snapshot databases. See [SQLite databases](../concepts/sqlite.md#mount-the-source-read-write).

!!! warning "A shutdown backup runs inside the grace period"

    The manifest above already takes a final backup with the `preStop` hook.
    `EZBAK_BACKUP_ON_SHUTDOWN: "true"` on the backup sidecar is an alternative
    that backs up on `SIGTERM` instead. Use one or the other, not both, or the
    pod takes two final backups. Either way Kubernetes holds the pod alive only
    for `terminationGracePeriodSeconds`, which the `preStop` hook also draws on,
    so raise it to cover the backup:

    ```yaml
    spec:
      terminationGracePeriodSeconds: 300
    ```

    If the backup outlasts the grace period, Kubernetes sends `SIGKILL` and the
    backup is lost.

!!! note "Match the volume to your workload"

    The example uses a `PersistentVolumeClaim` for `data`. Use the volume type
    your workload needs; the ezbak tasks only require that the restore container
    can write to it and the backup containers can read it.

## Forcing an on-demand backup

Signal the sidecar to back up right now, without waiting for its cron
schedule:

```bash
kubectl exec <pod> -c backup -- kill -USR1 1
```

See [Forcing an on-demand backup](../guides/docker.md#forcing-an-on-demand-backup)
for what the signal does and how it behaves.

For the same pattern on Nomad, see the [Nomad example](nomad.md).
