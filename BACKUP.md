# Noca backup and restore

Noca provides unattended scripts that back up and restore PostgreSQL, Valkey,
the bind-mounted application data, and the deployment configuration. The
backup output is a self-contained directory that you can send to an encrypted
remote Restic repository.

## Prerequisites

Run the scripts on the Docker Compose host. The host must meet these
requirements:

- Bash, Docker with the Compose plugin, GNU `tar`, `sha256sum`, `flock`, and
  `realpath` are installed.
- Restic is installed at `/opt/scripts/restic`, or `NOCA_RESTIC_BINARY` points
  to its executable.
- The account running the scripts can use Docker and read or write the Noca
  deployment directories.
- The production directory contains `.env`, `.env.crypto`, and one supported
  Compose filename: `docker-compose.yaml`, `docker-compose.yml`, `compose.yaml`,
  or `compose.yml`.
- The `problem_statements`, `problem_testcases`, and `email_log` directories
  exist in the production directory.
- The Compose project defines services named `postgres` and `valkey`.
- The configured S3 endpoint and bucket are reachable.
- Sufficient local space is available for the database dump, Valkey RDB file,
  and filesystem archive.

The scripts set a restrictive `umask`, but the backup contains credentials,
cryptographic configuration, and application data. Restrict access to the
backup directory and Restic repository. Keep S3 and Restic credentials outside
the repository in a protected scheduler or service-manager environment.

## Create a backup

Export the required Restic configuration, then run the backup script from the
production directory:

```bash
AWS_ACCESS_KEY_ID='S3_ACCESS_KEY' \
AWS_SECRET_ACCESS_KEY='S3_SECRET_KEY' \
RESTIC_REPOSITORY='s3:https://s3.example.com/noca-backup' \
RESTIC_PASSWORD='RESTIC_REPOSITORY_PASSWORD' \
RESTIC_HOST='noca.example.com' \
/path/to/noca/scripts/backup_noca.sh
```

By default, the script treats the current directory as the deployment
directory and writes snapshots under `./backups`. Set these environment
variables when either location differs:

```bash
AWS_ACCESS_KEY_ID='S3_ACCESS_KEY' \
AWS_SECRET_ACCESS_KEY='S3_SECRET_KEY' \
RESTIC_REPOSITORY='s3:https://s3.example.com/noca-backup' \
RESTIC_PASSWORD='RESTIC_REPOSITORY_PASSWORD' \
RESTIC_HOST='noca.example.com' \
NOCA_PROJECT_DIR=/srv/noca \
NOCA_BACKUP_DIR=/srv/noca/backups \
/opt/noca/scripts/backup_noca.sh
```

The script performs these operations without prompting:

1. Acquires an exclusive `.backup.lock` file in the backup root.
2. Stops only the currently running `web`, `arena`, `autojudge`, `rating`, and
   `aiassistant` services to prevent application writes during the backup.
3. Creates a PostgreSQL custom-format dump and a global-role dump.
4. Requests a consistent RDB snapshot from Valkey.
5. Archives the bind-mounted data, `.env`, `.env.crypto`, and Compose file.
6. Generates a manifest and SHA-256 checksums.
7. Publishes the completed backup atomically under a UTC timestamp and updates
   the `latest` symbolic link.
8. Restarts only the application services that were running before the backup.
9. Deletes the previous completed local backup, keeping only the newest one.
10. Uploads the backup root to Restic with up to three attempts.
11. Keeps seven daily, four weekly, and four monthly Restic snapshots.

If local backup creation fails after stopping application services, the exit
trap attempts to restart those services and removes the incomplete staging
directory. If the Restic upload fails, the completed local backup remains
available and the script returns a nonzero exit status.

### Backup contents

Each successful directory, such as `backups/20260620T212822Z`, contains these
artifacts:

| Artifact | Contents |
| --- | --- |
| `MANIFEST` | Backup format, creation timestamp, and Compose filename |
| `SHA256SUMS` | Integrity hashes for every required artifact |
| `postgres.dump` | PostgreSQL custom-format database dump |
| `postgres-globals.sql` | PostgreSQL roles without role passwords |
| `valkey.rdb` | Valkey RDB snapshot containing all logical databases |
| `filesystem.tar` | Application files, secrets, and deployment configuration |

The persistent `.backup.lock` in the backup root is expected. `flock` holds the
lock only while the backup process has its file descriptor open. Don't delete
the file as part of routine cleanup because a concurrent process can still be
using its inode.

## Restic upload and retention

The backup script uploads the backup root automatically after it completes the
local snapshot and restarts Noca. You don't need a separate Restic command.
The script requires these values in its process environment:

- `AWS_ACCESS_KEY_ID`: S3 access-key identifier
- `AWS_SECRET_ACCESS_KEY`: S3 secret access key
- `RESTIC_REPOSITORY`: complete Restic repository URL, including the endpoint
  and bucket
- `RESTIC_PASSWORD`: repository encryption password
- `RESTIC_HOST`: host name stored on snapshots and used by retention filtering

The operational behavior remains fixed:

- Snapshot tag: `noca`
- Upload attempts: three, with 120 seconds between failed attempts
- Remote retention: seven daily, four weekly, and four monthly snapshots

On each run, the script tries to read the Restic repository configuration. If
the repository isn't initialized, the script runs `restic init` before
continuing. It then runs `restic check` before it creates local backup
artifacts, removes stale Restic locks, uploads the backup root, and runs
`forget --prune` only after a successful upload.

If the endpoint is unavailable, the credentials are incorrect, or an existing
repository uses a different password, initialization or validation fails and
the script exits before stopping Noca services.

An unattended scheduler must inject the five required variables and invoke the
backup script. For example:

```bash
AWS_ACCESS_KEY_ID='S3_ACCESS_KEY' \
AWS_SECRET_ACCESS_KEY='S3_SECRET_KEY' \
RESTIC_REPOSITORY='s3:https://s3.example.com/noca-backup' \
RESTIC_PASSWORD='RESTIC_REPOSITORY_PASSWORD' \
RESTIC_HOST='noca.example.com' \
NOCA_PROJECT_DIR=/srv/noca \
NOCA_BACKUP_DIR=/srv/noca/backups \
/opt/noca/scripts/backup_noca.sh
```

The script keeps only the newest completed backup locally. Historical versions
are retained through Restic's snapshot policy. The persistent `.backup.lock`
and `latest` symbolic link are also included when Restic snapshots the backup
root.

## Restore a backup

Restore is destructive. It replaces the PostgreSQL database, Valkey data,
application filesystem directories, `.env`, `.env.crypto`, and the Compose
file with the selected backup.

<!-- prettier-ignore -->
> [!CAUTION]
> Don't run the restore script against a production deployment until you have
> selected and verified the intended backup. If restoration fails after the
> services stop, the script leaves them stopped so that you can inspect and
> correct the failure without accepting writes into a partial restore.

Use these steps before either local or remote restoration:

1. Ensure the Docker external networks referenced by the backed-up Compose file
   exist on the target host.
2. Ensure the target PostgreSQL and Valkey bind-mount locations are writable by
   their containers. On a new host, use empty data directories.

### Restore from a local backup

Pass a timestamped backup directory or a valid local `latest` link to restore
without contacting Restic:

```bash
NOCA_PROJECT_DIR=/srv/noca \
/opt/noca/scripts/restore_noca.sh \
    /srv/noca/backups/20260620T212822Z
```

### Restore a remote backup

Omit the backup-directory argument to download and restore the latest Restic
snapshot tagged `noca`. Provide the repository and credentials through the
environment:

```bash
AWS_ACCESS_KEY_ID='S3_ACCESS_KEY' \
AWS_SECRET_ACCESS_KEY='S3_SECRET_KEY' \
RESTIC_REPOSITORY='s3:https://s3.example.com/noca-backup' \
RESTIC_PASSWORD='RESTIC_REPOSITORY_PASSWORD' \
NOCA_PROJECT_DIR=/srv/noca \
/opt/noca/scripts/restore_noca.sh
```

To restore an older version, list the available `noca` snapshots after setting
the same four credential and repository environment variables:

```bash
/opt/scripts/restic snapshots --tag noca
```

Pass the selected short or full hexadecimal snapshot ID to the restore script:

```bash
AWS_ACCESS_KEY_ID='S3_ACCESS_KEY' \
AWS_SECRET_ACCESS_KEY='S3_SECRET_KEY' \
RESTIC_REPOSITORY='s3:https://s3.example.com/noca-backup' \
RESTIC_PASSWORD='RESTIC_REPOSITORY_PASSWORD' \
NOCA_PROJECT_DIR=/srv/noca \
/opt/noca/scripts/restore_noca.sh --snapshot 40dc1520
```

The remote mode uses `/opt/scripts/restic` by default. Set
`NOCA_RESTIC_BINARY` in the command environment when Restic is installed at a
different path. The script verifies repository access, restores the selected
snapshot with Restic verification, and selects its newest timestamped Noca
backup. It downloads remote data before stopping or modifying Noca.

For both modes, the restore script verifies every artifact checksum and the
backup format before it stops or changes services. It then performs these
operations:

1. Installs the backed-up environment and Compose configuration.
2. Stops the application services and Valkey.
3. Replaces `problem_statements`, `problem_testcases`, and `email_log`.
4. Starts PostgreSQL, recreates the configured database, and loads the dumps.
5. Replaces Valkey persistence with the backed-up RDB file.
6. Starts the complete Compose project and waits for its services.

The global-role dump deliberately excludes role passwords. On a clean
PostgreSQL volume, the container initializes the configured Noca role from the
restored `.env`. When restoring into an existing PostgreSQL cluster, its Noca
role password must already match `NOCA_DB_PASSWORD` in the restored `.env`.

## Verify a restore

After the script reports success, verify the deployment from the target
production directory:

```bash
docker compose ps
docker compose logs --since 10m \
    postgres valkey web arena autojudge rating aiassistant
```

Confirm that all expected services are running or healthy, then test login,
problem statement access, testcase judging, and any critical application flow
before returning the deployment to normal operation.
