# Production test-case storage migration

This runbook moves Web test cases into the `contest/` namespace, writes Arena
test-case content from PostgreSQL to the `arena/` namespace, and removes the
obsolete Arena content columns. It applies to a Docker Compose production
deployment upgrading through Alembic revisions `202606180004` and
`202606180005`.

The migration requires downtime for Web, Arena, and Autojudge. PostgreSQL,
Valkey, Rating, and AI Assistant can remain running.

<!-- prettier-ignore -->
> [!CAUTION]
> Do not start a new Web, Arena, or Autojudge container until this procedure
> applies revision `202606180005`. Each service entrypoint runs `alembic upgrade
> head`. Starting one early can drop the Arena content columns before their data
> has been copied to the filesystem.

## Understand the target layout

All three services must use one persistent filesystem root inside their
containers:

```text
/data/problem_test_cases/
├── contest/<problem_id>/NNN.in|out
└── arena/<problem_id>/NNN.in|out
```

Web and Arena require read/write access. Autojudge requires read-only access.
The host path behind `/data/problem_test_cases` must persist independently of
the containers.

## Prepare the deployment

Complete these preparations before opening the maintenance window.

1. Deploy or check out the release containing:

   - Alembic revisions `202606180004` and `202606180005`.
   - `scripts/backfill_web_tc_sizes.py`.
   - `scripts/migrate_arena_tc_to_fs.py`.
   - A Web image that copies both scripts into `/app/scripts`.

2. Replace the obsolete environment variables in `.env`:

   ```dotenv
   # Remove these variables:
   # NOCA_WEB_PROBLEM_TESTCASE_DIR=/data/problem_test_cases
   # NOCA_JUDGE_PROBLEM_TESTCASE_DIR=/data/problem_test_cases

   # Add this variable:
   NOCA_PROBLEM_TESTCASE_DIR=/data/problem_test_cases
   ```

3. Set the database host and port as separate values. The host must be
   reachable from the Compose network:

   ```dotenv
   NOCA_DB_SERVER=postgres
   NOCA_DB_PORT=5432
   ```

4. Update the production Compose file so every affected service mounts the
   same host directory:

   ```yaml
   services:
     web:
       environment:
         NOCA_PROBLEM_TESTCASE_DIR: /data/problem_test_cases
       volumes:
         - ${NOCA_DATA_ROOT:-./.docker}/problem_test_cases:/data/problem_test_cases

     arena:
       environment:
         NOCA_PROBLEM_TESTCASE_DIR: /data/problem_test_cases
       volumes:
         - ${NOCA_DATA_ROOT:-./.docker}/problem_test_cases:/data/problem_test_cases

     autojudge:
       environment:
         NOCA_PROBLEM_TESTCASE_DIR: /data/problem_test_cases
       volumes:
         - ${NOCA_DATA_ROOT:-./.docker}/problem_test_cases:/data/problem_test_cases:ro
   ```

5. Pull or build the release images without starting containers:

   ```bash
   docker compose pull web arena autojudge rating aiassistant
   ```

   If the deployment builds images locally, run this command instead:

   ```bash
   docker compose build web arena autojudge rating aiassistant
   ```

## Define the maintenance variables

Run the remaining commands from the repository directory that contains the
production Compose file. Set these variables to the values used by the
deployment:

```bash
export NOCA_DATA_ROOT=/srv/noca/data
export PUID=1000
export PGID=100
export TC_ROOT="$NOCA_DATA_ROOT/problem_test_cases"
export BACKUP_DIR="/srv/noca/backups/testcase-migration-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
```

`PUID` and `PGID` must match the application user configured in Compose. Do not
guess a custom data root or application user ID.

Define a helper that runs Python from the new Web image without invoking its
normal entrypoint:

```bash
run_web_python() {
    docker compose run --rm --no-deps \
        --user "$PUID:$PGID" \
        --entrypoint /app/.venv/bin/python \
        web "$@"
}
```

The entrypoint override is mandatory because the normal entrypoint upgrades
the database directly to `head`.

## Stop services and create backups

Stop every process that can create, edit, or judge test cases before taking
backups:

1. Stop Web, Arena, and Autojudge:

   ```bash
   docker compose stop web arena autojudge
   ```

2. Confirm that they are stopped:

   ```bash
   docker compose ps web arena autojudge
   ```

3. Back up PostgreSQL in custom archive format:

   ```bash
   docker compose exec -T postgres sh -c \
       'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
       > "$BACKUP_DIR/postgres.dump"
   test -s "$BACKUP_DIR/postgres.dump"
   ```

4. Back up the complete existing testcase directory:

   ```bash
   tar -C "$TC_ROOT" -czf "$BACKUP_DIR/problem_test_cases.tar.gz" .
   test -s "$BACKUP_DIR/problem_test_cases.tar.gz"
   ```

<!-- prettier-ignore -->
> [!IMPORTANT]
> Do not continue unless both backup files exist, are nonempty, and are stored
> outside the testcase directory.

## Verify the starting database revision

Check the current Alembic revision through the one-off Web container:

```bash
run_web_python -m alembic current
```

The expected revision is `202606180003`. Stop if the database is already at
`202606180005`, has multiple heads, or reports an unexpected revision. Resolve
the schema state before moving any files.

## Create the namespace directories

Create the two namespace directories with ownership matching the application
containers:

```bash
install -d -m 0775 -o "$PUID" -g "$PGID" \
    "$TC_ROOT/contest" "$TC_ROOT/arena"
```

If the deployment user cannot change ownership, run `install` with the
deployment's privilege escalation mechanism.

## Move existing Web test cases

Skip this move only when existing Web UUID directories are already below
`$TC_ROOT/contest`. Otherwise, move only UUID-named directories from the old
root layout:

```bash
shopt -s nullglob
for directory in "$TC_ROOT"/*/; do
    name="$(basename "$directory")"
    if [[ "$name" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
        mv -- "$directory" "$TC_ROOT/contest/"
    fi
done
shopt -u nullglob
```

Inspect the resulting top-level layout:

```bash
find "$TC_ROOT" -mindepth 1 -maxdepth 2 -type d -print | sort
```

The root must contain `contest/`, `arena/`, and no Web problem UUID directory.

## Backfill Web testcase sizes

Populate missing Web input and output byte sizes from the relocated files:

```bash
run_web_python /app/scripts/backfill_web_tc_sizes.py
```

The command must finish with `Web test-case size backfill complete`. A missing
file error means the Web files were not relocated to the expected directory.
Correct the filesystem layout, and rerun the script. The script is idempotent.

## Add Arena size columns

Apply Migration A, which adds nullable Arena size columns without deleting
content:

```bash
run_web_python -m alembic upgrade 202606180004
run_web_python -m alembic current
```

The current revision must now be `202606180004`.

## Write Arena test cases to the filesystem

Copy Arena input and output content from PostgreSQL into the `arena/`
namespace:

```bash
run_web_python /app/scripts/migrate_arena_tc_to_fs.py
```

The command must end with both of these messages:

```text
Arena test-case backfill complete: ...
Verification passed: per-problem file/row counts match.
```

If the command fails before verification passes, do not apply Migration B.
Correct the reported problem, and rerun the script. It safely skips rows that
already have both files and both size columns.

## Verify the backfill gate

Run database checks before deleting the source content columns:

```bash
docker compose exec -T postgres sh -c \
    'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
SELECT count(*) AS web_rows_with_missing_sizes
FROM test_cases
WHERE input_size_bytes IS NULL OR output_size_bytes IS NULL;

SELECT count(*) AS arena_rows_with_missing_sizes
FROM arena_test_cases
WHERE input_size_bytes IS NULL OR output_size_bytes IS NULL;
SQL
```

Both counts must be `0`. Also confirm that the Arena filesystem contains input
and output files when Arena test cases exist:

```bash
find "$TC_ROOT/arena" -type f -name '*.in' | wc -l
find "$TC_ROOT/arena" -type f -name '*.out' | wc -l
```

The migration script performs the authoritative per-problem comparison. These
totals are an additional operator check.

## Drop the Arena content columns

Apply Migration B only after every preceding verification succeeds:

```bash
run_web_python -m alembic upgrade 202606180005
run_web_python -m alembic current
```

The current revision must be `202606180005`.

<!-- prettier-ignore -->
> [!WARNING]
> Revision `202606180005` is the point of no return for an in-place rollback.
> Its downgrade recreates empty columns; it cannot restore the deleted Arena
> content. Recovery after this step requires the PostgreSQL backup.

## Start and verify the deployment

Start the new application containers only after Migration B succeeds:

1. Start the services:

   ```bash
   docker compose up -d web arena autojudge
   ```

2. Confirm that the containers become healthy:

   ```bash
   docker compose ps web arena autojudge
   ```

3. Inspect startup logs for configuration, permission, or migration errors:

   ```bash
   docker compose logs --since 10m web arena autojudge
   ```

4. Verify these production workflows:

   - Open an existing Web problem and inspect its test cases.
   - Submit a Web solution and confirm that Autojudge reads `contest/` files.
   - Open an existing Arena problem and inspect its sample test cases.
   - Submit an Arena solution and confirm that Autojudge reads `arena/` files.
   - Add or edit a testcase in both Web and Arena, if operational policy permits.

5. Keep both backups until the deployment has completed its normal retention
   and acceptance period.

## Recover from a failure

Choose recovery actions based on whether Migration B has run.

### Before Migration B

Do not downgrade immediately when a backfill fails. Both scripts are
idempotent, so correct filesystem ownership, missing files, or configuration,
and rerun the failed command.

If you must abandon the deployment, restore the database and testcase archive,
restore the old environment and Compose configuration, and restart the old
images. Restoring both backups avoids a mixed old-code/new-layout state.

### After Migration B

Stop all application and worker services. Restore the PostgreSQL dump and the
testcase archive together, restore the previous environment and Compose
configuration, and restart the previous images.

Do not rely on `alembic downgrade 202606180004` to recover Arena content. The
downgrade recreates the columns without repopulating their deleted values.
