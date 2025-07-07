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
