#!/usr/bin/env bash
# NOCA -- Next Online Contest Administrator
# Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# containers/arena/entrypoint.sh
#
# Wait for PostgreSQL and Valkey to be ready, run schema migrations under the
# shared PostgreSQL advisory lock, then start the Arena server.

set -euo pipefail

setup_runtime_user() {
    local target_uid="${PUID:-1000}"
    local target_gid="${PGID:-100}"
    local target_user="noca"
    local target_group="noca"
    local target_home="/home/${target_user}"
    local current_uid
    local current_gid
    local existing_group
    local existing_user

    [[ "$target_uid" =~ ^[0-9]+$ ]] || {
        echo "[arena-entrypoint] PUID must be numeric: ${target_uid}" >&2
        exit 1
    }
    [[ "$target_gid" =~ ^[0-9]+$ ]] || {
        echo "[arena-entrypoint] PGID must be numeric: ${target_gid}" >&2
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
        echo "[arena-entrypoint] startup requires root to switch to ${target_uid}:${target_gid}" >&2
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

    exec env HOME="$target_home" USER="$target_user" LOGNAME="$target_user" \
        setpriv --reuid="$target_uid" --regid="$target_gid" --clear-groups \
        "$0" "$@"
}

setup_runtime_user "$@"

cd /app

echo "[arena-entrypoint] waiting for PostgreSQL..."
/app/.venv/bin/python - <<'PY'
import asyncio
import os
from urllib.parse import quote

import asyncpg

MAX_ATTEMPTS = 60
SLEEP_SECONDS = 2


def build_db_url() -> str:
    user = quote(os.environ["NOCA_DB_USER"])
    password = quote(os.environ["NOCA_DB_PASSWORD"])
    server = os.environ["NOCA_DB_SERVER"]
    name = os.environ["NOCA_DB_NAME"]
    return f"postgresql://{user}:{password}@{server}/{name}"


async def wait_for_db() -> None:
    db_url = build_db_url()
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            conn = await asyncpg.connect(db_url)
            await conn.close()
            print(f"[arena-entrypoint] PostgreSQL is ready (attempt {attempt})")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[arena-entrypoint] PostgreSQL not ready (attempt {attempt}/{MAX_ATTEMPTS}): {exc}")
            await asyncio.sleep(SLEEP_SECONDS)

    raise RuntimeError(f"PostgreSQL did not become ready: {last_error}")


asyncio.run(wait_for_db())
PY

echo "[arena-entrypoint] waiting for Valkey..."
/app/.venv/bin/python - <<'PY'
import asyncio
import os
from urllib.parse import quote

import valkey.asyncio as aivalkey

MAX_ATTEMPTS = 60
SLEEP_SECONDS = 2


def build_valkey_url() -> str:
    server = os.environ["NOCA_VALKEY_SERVER"]
    port = os.environ.get("NOCA_VALKEY_PORT", "6379")
    db = os.environ.get("NOCA_VALKEY_DB", "0")
    user = os.environ.get("NOCA_VALKEY_USER", "")
    password = os.environ.get("NOCA_VALKEY_PASSWORD", "")

    auth = ""
    if user and password:
        auth = f"{quote(user)}:{quote(password)}@"
    elif password:
        auth = f":{quote(password)}@"

    return f"redis://{auth}{server}:{port}/{db}"


async def wait_for_valkey() -> None:
    valkey_url = build_valkey_url()
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        client = None
        try:
            client = aivalkey.Valkey.from_url(valkey_url, decode_responses=True)
            await client.ping()
            await client.aclose()
            print(f"[arena-entrypoint] Valkey is ready (attempt {attempt})")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[arena-entrypoint] Valkey not ready (attempt {attempt}/{MAX_ATTEMPTS}): {exc}")
            await asyncio.sleep(SLEEP_SECONDS)
        finally:
            if client is not None:
                await client.aclose()

    raise RuntimeError(f"Valkey did not become ready: {last_error}")


asyncio.run(wait_for_valkey())
PY

echo "Running Alembic migrations..."
python scripts/run_migrations.py

if [[ -n "${NOCA_ARENA_ADMIN_EMAIL:-}" ]]; then
    echo "Bootstrapping Arena admin..."
    python scripts/arena/create_arena_admin.py
else
    echo "Skipping Arena admin seed (NOCA_ARENA_ADMIN_EMAIL is not set)"
fi

if [[ "${NOCA_SEED_LANGUAGES:-false}" == "true" ]]; then
    echo "Bootstrapping languages..."
    python scripts/bootstrap_languages.py
else
    echo "Skipping language seed (NOCA_SEED_LANGUAGES is not 'true')"
fi

if [[ "$#" -eq 0 ]]; then
    set -- noca-arena
fi

exec "$@"
