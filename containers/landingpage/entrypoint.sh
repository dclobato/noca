#!/bin/sh
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

set -eu

for variable_name in \
    NOCA_LANDINGPAGE_WEB_URL \
    NOCA_LANDINGPAGE_ARENA_URL \
    NOCA_LANDINGPAGE_ANIMATOR_URL \
    NOCA_LANDINGPAGE_HEALTHMON_URL
do
    variable_value="$(printenv "$variable_name" 2>/dev/null || true)"
    case "$variable_value" in
        http://?* | https://?*) ;;
        *)
            echo "[landingpage-entrypoint] $variable_name must be an absolute HTTP(S) URL" >&2
            exit 1
            ;;
    esac
    case "$variable_value" in
        *[[:space:]]*)
            echo "[landingpage-entrypoint] $variable_name must not contain whitespace" >&2
            exit 1
            ;;
    esac
done

# The footer states the version of this deployment. There is no application to
# ask, so the value is configuration and is validated like the URLs: a landing
# page that silently claims no version is worse than one that refuses to start.
landingpage_version="$(printenv NOCA_LANDINGPAGE_VERSION 2>/dev/null || true)"
case "$landingpage_version" in
    "")
        echo "[landingpage-entrypoint] NOCA_LANDINGPAGE_VERSION must be set" >&2
        echo "[landingpage-entrypoint] (set it to the release tag being deployed)" >&2
        exit 1
        ;;
    *[[:space:]]*)
        echo "[landingpage-entrypoint] NOCA_LANDINGPAGE_VERSION must not contain whitespace" >&2
        exit 1
        ;;
esac

if [ "${#landingpage_version}" -gt 32 ]; then
    echo "[landingpage-entrypoint] NOCA_LANDINGPAGE_VERSION must be at most 32 characters" >&2
    exit 1
fi

exec "$@"
