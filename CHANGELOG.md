## v1.2.1 (2026-07-10)

### Fix

- **s3**: harden S3 transfers and restores
- **restore**: never chown a symlink's target
- **hooks**: log configured hooks at boot

### Refactor

- **checksums**: drop unused sha256_file

### Perf

- **container**: drop delay after one-shot runs
- **backup**: avoid re-index when logging prunes
- **backup**: cut per-file cost of tar filtering
- **backup**: default gzip compression to 6

## v1.2.0 (2026-07-10)

### Feat

- **hooks**: add container backup and restore lifecycle hooks (#64)

### Fix

- **backup**: match backup set by exact name

## v1.1.0 (2026-07-10)

### Feat

- **checksums**: add use_checksums master switch

### Fix

- **backup**: archive the complete source tree

### Perf

- **checksums**: hash archive in one read pass (#63)

## v1.0.1 (2026-07-09)

### Fix

- **logging**: trace retention keep/purge decisions
- **logging**: trace checksum and restore steps
- **container**: log ezbak version on startup

## v1.0.0 (2026-07-09)

### BREAKING CHANGE

- The retention configuration surface is renamed and
restructured across the library, CLI, and container. The three
mutually-exclusive policies (count-based max_backups, time-based
retention_*, and keep-all) are removed in favor of a union of
independent keep rules; a backup is retained if any rule keeps it.
Configuration using the old options must be migrated to the new keep
rules.
- renamed public options across the CLI, environment
variables, library config, and one exception attribute:
- restore --destination -> --restore-path
- restore --date -> --restore-date
- restore --clean -> --clean-before-restore
- --s3-bucket-path -> --s3-bucket-prefix
- aws_s3_bucket_path -> aws_s3_bucket_prefix (EZBAK_AWS_S3_BUCKET_PREFIX)
- delete_src_after_backup -> delete_source_after_backup
- BackupFailedError.failed_destinations -> failed_storage_locations

### Feat

- **s3**: add region and endpoint settings (#62)
- **container**: make cron jitter configurable (#61)
- **retention**: replace policies with union keep rules (#59)
- **container**: opt-in backup on shutdown
- add archive integrity checksum sidecars (#58)
- **restore**: skip cleanly when no backup exists
- **restore**: add point-in-time restore by date (#55)
- **cli**: add --dry-run to preview prune
- **container**: ping healthcheck on scheduled runs

### Fix

- **restore**: make restore atomic and guard storage overlap (#57)
- **prune**: report backups actually deleted, not targeted
- expose successful backups on partial failure
- exit cleanly on invalid configuration
- fail loudly when a restore fails (#54)
- fail loudly when a backup destination is unusable (#53)
- remove cosmetic filename time-unit labels (#52)
- **backup**: handle missing temp archive

### Refactor

- **config**: clarify option naming
- restructure into one config schema, core, and adapters (#51)
- **storage**: add backend abstraction
- **backups**: de-duplicate index and rename logic
- **naming**: centralize backup filename grammar
- **cli**: move app factory out of package init
- move RetentionPolicyManager into models layer

## v0.12.4 (2026-06-24)

### Fix

- **logs**: improve trace logging

## v0.12.3 (2026-06-22)

### Fix

- **prune**: skip and log backups already deleted

## v0.12.2 (2026-05-07)

### Fix

- **app**: ensure all errors propagate to logs(#46)
- replace nclutils logger with loguru (#45)

## v0.12.1 (2026-05-05)

### Refactor

- migrate off deprecated whenever APIs (#43)

## v0.12.0 (2025-12-26)

### Feat

- support python 3.14 (#37)

### Fix

- cleanup temp files

## v0.11.6 (2025-10-28)

### Fix

- fix typo breaking release

## v0.11.5 (2025-10-28)

### Fix

- adjust log level for backup timestamp (#35)

## v0.11.4 (2025-08-11)

### Fix

- **cli**: remove completions (#32)
- suppress secrets from debug logs (#30)

### Refactor

- shift to instance-based application model (#31)

## v0.11.3 (2025-07-07)

### Fix

- **docker**: specify uv version in dockerfile (#29)
- add logging of restore location (#28)

## v0.11.2 (2025-07-02)

### Fix

- **docker**: remove --locked from uv sync (#27)

## v0.11.1 (2025-07-02)

### Fix

- **ci**: fix release workflow

## v0.11.0 (2025-07-02)

### Feat

- **backup**: include empty directories in archive (#26)

## v0.10.2 (2025-06-30)

### Fix

- fix broken release workflow

## v0.10.1 (2025-06-30)

### Fix

- move source path validation to BackupManager (#24)

### Refactor

- **docker**: optimize build with layer caching (#23)

## v0.10.0 (2025-06-27)

### Feat

- drop support for mongodump (#22)

### Fix

- correct error updating storage location

## v0.9.0 (2025-06-27)

### Feat

- add delete_src_after_backup option (#21)

## v0.8.3 (2025-06-25)

### Fix

- **docker**: fix reduce memory for long running deployments

## v0.8.2 (2025-06-25)

### Fix

- **cli**: improve command output (#20)
- decrease backup indexing frequency (#19)

## v0.8.1 (2025-06-25)

### Fix

- fix error uploading to S3

## v0.8.0 (2025-06-25)

### Feat

- support S3 as a storage location (#16)
- support mongodb backups (#15)
- **docker**: log cron next run (#14)

### Fix

- improve logging (#18)
- add aws options to ezbak package (#17)
- rename env variables (#13)

## v0.7.0 (2025-06-22)

### Feat

- add option to flatten source paths (#10)

### Fix

- **entrypoint**: add version number to debug logs (#11)

## v0.6.3 (2025-06-22)

### Fix

- **docker**: run ezbak directly

## v0.6.2 (2025-06-22)

### Fix

- **docker**: improve docker load speed

## v0.6.1 (2025-06-21)

### Fix

- **docker**: reduce cpu usage

## v0.6.0 (2025-06-21)

### Feat

- **cli**: add restore command

## v0.5.0 (2025-06-21)

### Feat

- **logging**: support logger prefix (#9)
- add docker container (#8)
- rename restore method to restore_latest_backup (#7)
- **restore**: add option to pre-clean restore directory (#6)
- add exclude list of files (#2)

### Fix

- rename arguments for clarity (#5)

### Refactor

- use a global settings object (#4)

## v0.4.0 (2025-06-17)

### Feat

- rename files with and without time labels

## v0.3.0 (2025-06-17)

### Feat

- **restore**: change ownership of restored files

## v0.2.1 (2025-06-16)

### Fix

- accept strings or Path for logfile location

## v0.2.0 (2025-06-16)

### Feat

- initial commit
