#!/usr/bin/env bash
# NOCA -- Next Online Contest Administrator
# Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# containers/autojudge/entrypoint.sh
#
# Wait for PostgreSQL and Valkey to be ready, run schema migrations under the
# shared PostgreSQL advisory lock, then start the judge worker.

set -euo pipefail

setup_runtime_user() {
    local target_uid="${PUID:-1000}"
    local target_gid="${PGID:-100}"
    local target_user="noca"
    local target_group="noca"
    local target_home="/home/${target_user}"
    local testcase_dir="${NOCA_PROBLEM_TESTCASE_DIR:?NOCA_PROBLEM_TESTCASE_DIR must be set}"
    local docker_socket="/var/run/docker.sock"
    local docker_socket_gid=""
    local current_uid
    local current_gid
    local existing_group
    local existing_user
    local extra_groups=""

    [[ "$target_uid" =~ ^[0-9]+$ ]] || {
        echo "[autojudge-entrypoint] PUID must be numeric: ${target_uid}" >&2
        exit 1
    }
    [[ "$target_gid" =~ ^[0-9]+$ ]] || {
        echo "[autojudge-entrypoint] PGID must be numeric: ${target_gid}" >&2
        exit 1
    }

    current_uid="$(id -u)"
    current_gid="$(id -g)"
    if [[ "$current_uid" == "$target_uid" && "$current_gid" == "$target_gid" ]]; then
        export HOME="${HOME:-$target_home}"
        export USER="${USER:-$target_user}"
        export LOGNAME="${LOGNAME:-$target_user}"
        return
    fi

    if [[ "$current_uid" != "0" ]]; then
        echo "[autojudge-entrypoint] startup requires root to switch to ${target_uid}:${target_gid}" >&2
        exit 1
    fi

    existing_group="$(getent group "$target_gid" | cut -d: -f1 || true)"
    if [[ -n "$existing_group" ]]; then
        target_group="$existing_group"
    elif getent group "$target_group" >/dev/null; then
        groupmod -g "$target_gid" "$target_group"
    else
        groupadd -g "$target_gid" "$target_group"
    fi

    existing_user="$(getent passwd "$target_uid" | cut -d: -f1 || true)"
    if [[ -n "$existing_user" ]]; then
        target_user="$existing_user"
        target_home="$(getent passwd "$target_uid" | cut -d: -f6)"
    elif getent passwd "$target_user" >/dev/null; then
        usermod -u "$target_uid" -g "$target_gid" -d "$target_home" "$target_user"
    else
        useradd -u "$target_uid" -g "$target_gid" -m -d "$target_home" "$target_user"
    fi

    mkdir -p "$target_home" /tmp/uv-cache
    chown -R "$target_uid:$target_gid" "$target_home" /tmp/uv-cache

    if [[ ! -d "$testcase_dir" ]]; then
        mkdir -p "$testcase_dir"
    fi

    append_group_id() {
        local group_id="$1"

        if [[ -z "$group_id" || "$group_id" == "0" || "$group_id" == "$target_gid" ]]; then
            return
        fi
        if [[ ",$extra_groups," == *",$group_id,"* ]]; then
            return
        fi
        if [[ -n "$extra_groups" ]]; then
            extra_groups+=","
        fi
        extra_groups+="$group_id"
    }

    for group_id in $(id -G); do
        append_group_id "$group_id"
    done

    if [[ -S "$docker_socket" ]]; then
        docker_socket_gid="$(stat -c '%g' "$docker_socket")"
        append_group_id "$docker_socket_gid"
    fi

    if [[ -n "$extra_groups" ]]; then
        exec env HOME="$target_home" USER="$target_user" LOGNAME="$target_user" \
            setpriv --reuid="$target_uid" --regid="$target_gid" --groups="$extra_groups" \
            "$0" "$@"
    fi

    exec env HOME="$target_home" USER="$target_user" LOGNAME="$target_user" \
        setpriv --reuid="$target_uid" --regid="$target_gid" --clear-groups \
        "$0" "$@"
}

setup_runtime_user "$@"

# ---------------------------------------------------------------------------
# Wait for PostgreSQL
# ---------------------------------------------------------------------------
DB_HOST="${NOCA_DB_SERVER:-postgres}"
DB_PORT="${NOCA_DB_PORT:-5432}"
MAX_ATTEMPTS=60
ATTEMPT=0

echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
while ! bash -c "echo > /dev/tcp/${DB_HOST}/${DB_PORT}" 2>/dev/null; do
    ATTEMPT=$((ATTEMPT + 1))
    if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
        echo "ERROR: PostgreSQL not reachable after ${MAX_ATTEMPTS} attempts."
        exit 1
    fi
    sleep 2
done
echo "PostgreSQL is ready."

# ---------------------------------------------------------------------------
# Wait for Valkey
# ---------------------------------------------------------------------------
VALKEY_HOST="${NOCA_VALKEY_SERVER:-valkey}"
VALKEY_PORT="${NOCA_VALKEY_PORT:-6379}"
ATTEMPT=0

echo "Waiting for Valkey at ${VALKEY_HOST}:${VALKEY_PORT}..."
while ! bash -c "echo > /dev/tcp/${VALKEY_HOST}/${VALKEY_PORT}" 2>/dev/null; do
    ATTEMPT=$((ATTEMPT + 1))
    if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
        echo "ERROR: Valkey not reachable after ${MAX_ATTEMPTS} attempts."
        exit 1
    fi
    sleep 2
done
echo "Valkey is ready."

# ---------------------------------------------------------------------------
# Run DB migrations
# ---------------------------------------------------------------------------
echo "Running Alembic migrations..."
python scripts/run_migrations.py

# ---------------------------------------------------------------------------
# Start the worker
# ---------------------------------------------------------------------------
exec "$@"
