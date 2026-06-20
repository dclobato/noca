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
readonly BACKUP_RETENTION_COUNT=1
readonly RESTIC_MAX_RETRIES=3
readonly RESTIC_RETRY_DELAY_SECONDS=120
readonly TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly RESTIC_BINARY="${NOCA_RESTIC_BINARY:-/opt/scripts/restic}"

# Run from the production Compose directory. Override NOCA_PROJECT_DIR and
# NOCA_BACKUP_DIR when the deployment or Restic source directory is elsewhere.

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

require_restic_environment() {
    local variable_name

    for variable_name in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY \
        RESTIC_REPOSITORY RESTIC_PASSWORD RESTIC_HOST; do
        [[ -n "${!variable_name:-}" ]] || \
            die "Backup requires $variable_name"
        export "$variable_name"
    done
}

find_compose_file() {
    local candidate
    for candidate in docker-compose.yaml docker-compose.yml compose.yaml compose.yml; do
        if [[ -f "$PROJECT_DIR/$candidate" ]]; then
            printf '%s\n' "$PROJECT_DIR/$candidate"
            return
        fi
    done
    die "No Compose file found in $PROJECT_DIR"
}

read_dotenv_value() {
    local key="$1"
    local pattern_prefix
    local value

    pattern_prefix="^[[:space:]]*(export[[:space:]]+)?${key}"
    value="$(sed -n -E \
        "s/${pattern_prefix}[[:space:]]*=[[:space:]]*(.*)$/\\2/p" \
        "$PROJECT_DIR/.env" | tail -n 1)"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
        value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
        value="${value:1:${#value}-2}"
    else
        value="${value%%[[:space:]]#*}"
    fi
    printf '%s' "$value"
}

service_exists() {
    local wanted="$1"
    local service
    for service in "${COMPOSE_SERVICES[@]}"; do
        [[ "$service" == "$wanted" ]] && return 0
    done
    return 1
}

start_stopped_services() {
    if ((${#STOPPED_SERVICES[@]} > 0)); then
        log "Restarting application services"
        "${COMPOSE[@]}" up -d "${STOPPED_SERVICES[@]}" || \
            log "ERROR: failed to restart one or more application services"
        STOPPED_SERVICES=()
    fi
}

prune_old_backups() {
    local candidate
    local directory_name
    local delete_count
    local index
    local -a backup_directories=()

    # Shell pathname expansion orders these fixed-width UTC timestamps oldest first.
    for candidate in "$BACKUP_ROOT"/*; do
        [[ -d "$candidate" && ! -L "$candidate" ]] || continue
        directory_name="${candidate##*/}"
        [[ "$directory_name" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || continue
        backup_directories+=("$candidate")
    done

    delete_count=$((${#backup_directories[@]} - BACKUP_RETENTION_COUNT))
    if ((delete_count <= 0)); then
        return
    fi

    log "Removing $delete_count obsolete local backup(s); retaining newest $BACKUP_RETENTION_COUNT"
    for ((index = 0; index < delete_count; index++)); do
        rm -rf -- "${backup_directories[$index]}"
    done
}

ensure_restic_repository() {
    log "Checking Restic repository configuration"
    if ! "$RESTIC_BINARY" cat config >/dev/null 2>&1; then
        log "Restic repository is not initialized; creating it"
        "$RESTIC_BINARY" init || die "Restic repository initialization failed"
    fi

    log "Checking Restic repository integrity"
    "$RESTIC_BINARY" check || die "Restic repository check failed"
}

upload_backups_to_restic() {
    local attempt

    log "Removing stale Restic locks"
    "$RESTIC_BINARY" unlock

    for ((attempt = 1; attempt <= RESTIC_MAX_RETRIES; attempt++)); do
        log "Uploading backups to Restic (attempt $attempt/$RESTIC_MAX_RETRIES)"
        if "$RESTIC_BINARY" backup --tag noca "$BACKUP_ROOT"; then
            log "Restic upload completed"
            return
        fi
        if ((attempt < RESTIC_MAX_RETRIES)); then
            log "Restic upload failed; retrying in $RESTIC_RETRY_DELAY_SECONDS seconds"
            sleep "$RESTIC_RETRY_DELAY_SECONDS"
        fi
    done

    die "Restic upload failed after $RESTIC_MAX_RETRIES attempts"
}

apply_restic_retention() {
    log "Applying Restic retention policy"
    "$RESTIC_BINARY" forget --prune --host "$RESTIC_HOST" --tag noca \
        --keep-daily 7 --keep-weekly 4 --keep-monthly 4
}

cleanup() {
    local exit_status=$?
    start_stopped_services
    if [[ -n "${STAGING_DIR:-}" && -d "$STAGING_DIR" ]]; then
        rm -rf -- "$STAGING_DIR"
    fi
    exit "$exit_status"
}

PROJECT_DIR="${NOCA_PROJECT_DIR:-$(pwd -P)}"
BACKUP_ROOT="${NOCA_BACKUP_DIR:-$PROJECT_DIR/backups}"

require_command docker
require_command tar
require_command sha256sum
require_command flock
require_command "$RESTIC_BINARY"
require_restic_environment
[[ -f "$PROJECT_DIR/.env" ]] || die "Missing $PROJECT_DIR/.env"
[[ -f "$PROJECT_DIR/.env.crypto" ]] || die "Missing $PROJECT_DIR/.env.crypto"

COMPOSE_FILE="$(find_compose_file)"
COMPOSE=(docker compose --project-directory "$PROJECT_DIR" --env-file "$PROJECT_DIR/.env" \
    -f "$COMPOSE_FILE")
mapfile -t COMPOSE_SERVICES < <("${COMPOSE[@]}" config --services)
mapfile -t RUNNING_SERVICES < <("${COMPOSE[@]}" ps --status running --services)
service_exists postgres || die "Compose service 'postgres' is required"
service_exists valkey || die "Compose service 'valkey' is required"

readonly FILESYSTEM_DATA_PATHS=(problem_statements problem_testcases email_log)
readonly FILESYSTEM_CONFIG_PATHS=(.env .env.crypto "${COMPOSE_FILE#"$PROJECT_DIR/"}")
readonly FILESYSTEM_PATHS=("${FILESYSTEM_DATA_PATHS[@]}" "${FILESYSTEM_CONFIG_PATHS[@]}")
for path in "${FILESYSTEM_PATHS[@]}"; do
    [[ -e "$PROJECT_DIR/$path" ]] || die "Required backup path is missing: $PROJECT_DIR/$path"
done

mkdir -p -- "$BACKUP_ROOT"
exec 9>"$BACKUP_ROOT/.backup.lock"
flock -n 9 || die "Another Noca backup is already running"

FINAL_DIR="$BACKUP_ROOT/$TIMESTAMP"
[[ ! -e "$FINAL_DIR" ]] || die "Backup directory already exists: $FINAL_DIR"
STAGING_DIR="$(mktemp -d "$BACKUP_ROOT/.${TIMESTAMP}.tmp.XXXXXX")"
STOPPED_SERVICES=()
trap cleanup EXIT INT TERM HUP

ensure_restic_repository

for service in web arena autojudge rating aiassistant; do
    for running_service in "${RUNNING_SERVICES[@]}"; do
        if [[ "$running_service" == "$service" ]]; then
            STOPPED_SERVICES+=("$service")
            break
        fi
    done
done

log "Quiescing application services"
if ((${#STOPPED_SERVICES[@]} > 0)); then
    "${COMPOSE[@]}" stop "${STOPPED_SERVICES[@]}"
fi

log "Backing up PostgreSQL"
"${COMPOSE[@]}" exec -T postgres sh -euc \
    'pg_dump --username "$POSTGRES_USER" --format=custom --compress=6 "$POSTGRES_DB"' \
    >"$STAGING_DIR/postgres.dump"
"${COMPOSE[@]}" exec -T postgres sh -euc \
    'pg_dumpall --username "$POSTGRES_USER" --globals-only --no-role-passwords' \
    >"$STAGING_DIR/postgres-globals.sql"

log "Backing up Valkey"
VALKEY_RDB_PATH="/tmp/noca-backup-$TIMESTAMP.rdb"
VALKEY_USER="$(read_dotenv_value NOCA_VALKEY_USER)"
VALKEY_PASSWORD="$(read_dotenv_value NOCA_VALKEY_PASSWORD)"
VALKEY_COMMAND=("${COMPOSE[@]}" exec -T)
if [[ -n "$VALKEY_PASSWORD" ]]; then
    VALKEY_COMMAND+=(-e "VALKEYCLI_AUTH=$VALKEY_PASSWORD")
fi
VALKEY_COMMAND+=(valkey valkey-cli)
if [[ -n "$VALKEY_USER" ]]; then
    VALKEY_COMMAND+=(--user "$VALKEY_USER")
fi
"${VALKEY_COMMAND[@]}" --rdb "$VALKEY_RDB_PATH"
"${COMPOSE[@]}" cp "valkey:$VALKEY_RDB_PATH" "$STAGING_DIR/valkey.rdb"
"${COMPOSE[@]}" exec -T valkey rm -f -- "$VALKEY_RDB_PATH"

log "Archiving Noca filesystem data"
tar --create --file="$STAGING_DIR/filesystem.tar" --acls --xattrs \
    --numeric-owner --directory="$PROJECT_DIR" -- "${FILESYSTEM_DATA_PATHS[@]}"
tar --append --file="$STAGING_DIR/filesystem.tar" --acls --xattrs --dereference \
    --numeric-owner --directory="$PROJECT_DIR" -- "${FILESYSTEM_CONFIG_PATHS[@]}"

cat >"$STAGING_DIR/MANIFEST" <<EOF
backup_format=$BACKUP_FORMAT_VERSION
created_at=$TIMESTAMP
compose_file=${COMPOSE_FILE#"$PROJECT_DIR/"}
EOF
(
    cd "$STAGING_DIR"
    sha256sum MANIFEST filesystem.tar postgres.dump postgres-globals.sql valkey.rdb \
        >SHA256SUMS
)

mv -- "$STAGING_DIR" "$FINAL_DIR"
STAGING_DIR=""
ln -sfn -- "$TIMESTAMP" "$BACKUP_ROOT/latest"
start_stopped_services
prune_old_backups
upload_backups_to_restic
apply_restic_retention
trap - EXIT INT TERM HUP

log "Local and remote backup completed: $FINAL_DIR"
