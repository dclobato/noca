#!/usr/bin/env bash
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

set -Eeuo pipefail
umask 077

readonly SCRIPT_NAME="${0##*/}"
readonly BACKUP_FORMAT_VERSION="1"
readonly RESTIC_BINARY="${NOCA_RESTIC_BINARY:-/opt/scripts/restic}"

# Run from the target production Compose directory. Pass a local backup
# directory, or omit it and provide the Restic and AWS environment variables.

log() {
    printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SCRIPT_NAME" "$*" >&2
}

die() {
    log "ERROR: $*"
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

print_usage() {
    printf 'Usage: %s [BACKUP_DIRECTORY]\n' "$SCRIPT_NAME" >&2
    printf '       %s --snapshot SNAPSHOT_ID\n' "$SCRIPT_NAME" >&2
}

manifest_value() {
    local key="$1"
    sed -n -E "s/^${key}=(.*)$/\\1/p" "$BACKUP_DIR/MANIFEST" | tail -n 1
}

service_exists() {
    local wanted="$1"
    local service
    for service in "${COMPOSE_SERVICES[@]}"; do
        [[ "$service" == "$wanted" ]] && return 0
    done
    return 1
}

remove_temporary_directories() {
    if [[ -n "${EXTRACT_DIR:-}" && -d "$EXTRACT_DIR" ]]; then
        rm -rf -- "$EXTRACT_DIR"
        EXTRACT_DIR=""
    fi
    if [[ -n "${RESTIC_RESTORE_DIR:-}" && -d "$RESTIC_RESTORE_DIR" ]]; then
        rm -rf -- "$RESTIC_RESTORE_DIR"
        RESTIC_RESTORE_DIR=""
    fi
}

require_remote_environment() {
    local variable_name

    for variable_name in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY \
        RESTIC_REPOSITORY RESTIC_PASSWORD; do
        [[ -n "${!variable_name:-}" ]] || \
            die "Remote restore requires $variable_name"
        export "$variable_name"
    done
}

select_latest_restored_backup() {
    local candidate
    local candidate_name
    local latest_directory=""
    local latest_name=""

    while IFS= read -r -d '' candidate; do
        candidate_name="${candidate##*/}"
        [[ "$candidate_name" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || continue
        if [[ -z "$latest_name" || "$candidate_name" > "$latest_name" ]]; then
            latest_name="$candidate_name"
            latest_directory="$candidate"
        fi
    done < <(find "$RESTIC_RESTORE_DIR" -type d -print0)

    [[ -n "$latest_directory" ]] || \
        die "Restic snapshot contains no timestamped Noca backup"
    BACKUP_DIR="$latest_directory"
}

download_restic_backup() {
    local -a restore_command

    require_command "$RESTIC_BINARY"
    require_remote_environment

    log "Checking Restic repository access"
    "$RESTIC_BINARY" cat config >/dev/null || die "Cannot access Restic repository"

    RESTIC_RESTORE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/noca-restic-restore.XXXXXX")"
    log "Downloading Restic snapshot: $RESTIC_SNAPSHOT"
    restore_command=("$RESTIC_BINARY" restore "$RESTIC_SNAPSHOT" --verify \
        --target "$RESTIC_RESTORE_DIR")
    if [[ "$RESTIC_SNAPSHOT" == latest ]]; then
        restore_command+=(--tag noca)
    fi
    "${restore_command[@]}"
    select_latest_restored_backup
    RESTORE_SOURCE_DESCRIPTION="Restic snapshot $RESTIC_SNAPSHOT (${BACKUP_DIR##*/})"
    log "Selected remote backup: ${BACKUP_DIR##*/}"
}

cleanup() {
    local exit_status=$?
    remove_temporary_directories
    if ((RESTORE_STARTED && exit_status != 0)); then
        log "ERROR: restore failed after services were stopped; inspect the logs before restarting"
    fi
    exit "$exit_status"
}

if (($# > 2)); then
    print_usage
    exit 2
fi
if (($# == 2)) && [[ "$1" != --snapshot ]]; then
    print_usage
    exit 2
fi

PROJECT_DIR="${NOCA_PROJECT_DIR:-$(pwd -P)}"
RESTORE_STARTED=0
EXTRACT_DIR=""
RESTIC_RESTORE_DIR=""
RESTORE_SOURCE_DESCRIPTION=""
RESTIC_SNAPSHOT="latest"
trap cleanup EXIT INT TERM HUP

require_command docker
require_command tar
require_command sha256sum
require_command flock
require_command find
require_command realpath

if (($# == 2)); then
    [[ "$2" =~ ^[[:xdigit:]]{8,64}$ ]] || \
        die "Restic snapshot ID must contain 8 to 64 hexadecimal characters"
    RESTIC_SNAPSHOT="$2"
    download_restic_backup
elif (($# == 1)); then
    BACKUP_DIR="$(realpath "$1")"
    RESTORE_SOURCE_DESCRIPTION="$BACKUP_DIR"
else
    download_restic_backup
fi

for artifact in MANIFEST SHA256SUMS filesystem.tar postgres.dump postgres-globals.sql valkey.rdb; do
    [[ -f "$BACKUP_DIR/$artifact" ]] || die "Backup artifact is missing: $artifact"
done

log "Verifying backup checksums"
(cd "$BACKUP_DIR" && sha256sum --check --strict SHA256SUMS) || \
    die "Backup checksum verification failed"
[[ "$(manifest_value backup_format)" == "$BACKUP_FORMAT_VERSION" ]] || \
    die "Unsupported backup format: $(manifest_value backup_format)"

COMPOSE_RELATIVE="$(manifest_value compose_file)"
[[ -n "$COMPOSE_RELATIVE" && "$COMPOSE_RELATIVE" != /* && "$COMPOSE_RELATIVE" != *..* ]] || \
    die "Invalid Compose path in backup manifest"

EXTRACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/noca-restore.XXXXXX")"
tar --extract --file="$BACKUP_DIR/filesystem.tar" --acls --xattrs \
    --numeric-owner --directory="$EXTRACT_DIR"
for path in problem_statements problem_testcases email_log .env .env.crypto "$COMPOSE_RELATIVE"; do
    [[ -e "$EXTRACT_DIR/$path" ]] || die "Filesystem archive is missing required path: $path"
done

mkdir -p -- "$PROJECT_DIR"
exec 9>"$PROJECT_DIR/.restore.lock"
flock -n 9 || die "Another Noca restore is already running"

# Install the backed-up configuration first so Compose can initialize a clean host.
install -m 600 -- "$EXTRACT_DIR/.env" "$PROJECT_DIR/.env"
install -m 600 -- "$EXTRACT_DIR/.env.crypto" "$PROJECT_DIR/.env.crypto"
mkdir -p -- "$(dirname "$PROJECT_DIR/$COMPOSE_RELATIVE")"
install -m 600 -- "$EXTRACT_DIR/$COMPOSE_RELATIVE" "$PROJECT_DIR/$COMPOSE_RELATIVE"

COMPOSE=(docker compose --project-directory "$PROJECT_DIR" --env-file "$PROJECT_DIR/.env" \
    -f "$PROJECT_DIR/$COMPOSE_RELATIVE")
mapfile -t COMPOSE_SERVICES < <("${COMPOSE[@]}" config --services)
service_exists postgres || die "Backed-up Compose file has no 'postgres' service"
service_exists valkey || die "Backed-up Compose file has no 'valkey' service"

APPLICATION_SERVICES=()
for service in web arena autojudge rating aiassistant; do
    if service_exists "$service"; then
        APPLICATION_SERVICES+=("$service")
    fi
done

RESTORE_STARTED=1
log "Stopping Noca application and Valkey services"
if ((${#APPLICATION_SERVICES[@]} > 0)); then
    "${COMPOSE[@]}" stop "${APPLICATION_SERVICES[@]}"
fi
"${COMPOSE[@]}" stop valkey

log "Replacing filesystem data"
for path in problem_statements problem_testcases email_log; do
    mkdir -p -- "$PROJECT_DIR/$path"
    find "$PROJECT_DIR/$path" -mindepth 1 -delete
    cp -a -- "$EXTRACT_DIR/$path/." "$PROJECT_DIR/$path/"
done

log "Starting PostgreSQL"
"${COMPOSE[@]}" up -d --wait postgres

log "Restoring PostgreSQL globals and database"
# Existing roles make CREATE ROLE statements fail harmlessly; ALTER ROLE still applies.
"${COMPOSE[@]}" exec -T postgres sh -euc \
    'psql --username "$POSTGRES_USER" --dbname postgres' \
    <"$BACKUP_DIR/postgres-globals.sql"
"${COMPOSE[@]}" exec -T postgres sh -euc '
    dropdb --username "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB"
    createdb --username "$POSTGRES_USER" --owner "$POSTGRES_USER" "$POSTGRES_DB"
'
"${COMPOSE[@]}" exec -T postgres sh -euc \
    'pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --exit-on-error' \
    <"$BACKUP_DIR/postgres.dump"

log "Restoring Valkey RDB"
"${COMPOSE[@]}" run --rm --no-deps -T \
    --volume "$BACKUP_DIR/valkey.rdb:/restore/dump.rdb:ro" valkey sh -euc '
        rm -rf -- /data/appendonlydir /data/appendonly.aof
        cp -- /restore/dump.rdb /data/dump.rdb
    '

log "Starting all Noca services"
"${COMPOSE[@]}" up -d --wait
RESTORE_STARTED=0
remove_temporary_directories
trap - EXIT INT TERM HUP

log "Restore completed from: $RESTORE_SOURCE_DESCRIPTION"
