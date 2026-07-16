#!/bin/bash

AC=0
WA=1
TLE=2
RE=3
PE=4

finish() {
    local exitcode="$1"
    local message="${2:-}"
    if [[ -n "$message" ]]; then
        printf '%s\n' "$message" >&2
    fi
    exit "$exitcode"
}

# Reads one line, trimming surrounding whitespace. Returns 1 at end of input.
read_line() {
    local raw
    if ! IFS= read -r raw; then
        return 1
    fi
    raw="${raw#"${raw%%[![:space:]]*}"}"
    raw="${raw%"${raw##*[![:space:]]}"}"
    line="$raw"
    return 0
}

is_integer() {
    [[ "$1" =~ ^[+-]?[0-9]+$ ]]
}

env_number() {
    local name="$1"
    if [[ -z "${!name+set}" ]]; then
        printf 'null'
    else
        printf '%s' "${!name}"
    fi
}

print_limits() {
    local user_language per_language_limits
    if [[ -z "${USER_LANGUAGE+set}" ]]; then
        user_language='null'
    else
        user_language="\"${USER_LANGUAGE}\""
    fi
    if [[ -z "${PER_LANGUAGE_LIMITS+set}" ]]; then
        per_language_limits='null'
    else
        per_language_limits="${PER_LANGUAGE_LIMITS}"
    fi

    printf '{"problem_time_limit": %s, "problem_output_limit": %s, "problem_memory_limit": %s, ' \
        "$(env_number PROBLEM_TIME_LIMIT)" \
        "$(env_number PROBLEM_OUTPUT_LIMIT)" \
        "$(env_number PROBLEM_MEMORY_LIMIT)" >&2
    printf '"problem_pid_limit": %s, "user_language": %s, "per_language_limits": %s}\n' \
        "$(env_number PROBLEM_PID_LIMIT)" \
        "$user_language" \
        "$per_language_limits" >&2
}

read_secret_value() {
    if ! read_line || ! is_integer "$line"; then
        finish "$RE" 'Invalid secret data'
    fi
    printf '%s' "$line"
}

max_value="$(read_secret_value)" || exit $?
max_tries="$(read_secret_value)" || exit $?
secret="$(read_secret_value)" || exit $?

print_limits

printf '%s\n' "$max_value"

iteration=0
while read_line; do
    user="$line"

    if [[ "${user:0:1}" == '!' ]]; then
        answer="${user:1}"
        is_integer "$answer" || finish "$RE" 'Invalid data'
        if (( answer == secret )); then
            finish "$AC"
        fi
        finish "$WA" "!${secret}"
    fi

    if (( iteration > max_tries )); then
        finish "$TLE" 'Exceeded number of allowed iterations'
    fi
    is_integer "$user" || finish "$RE" 'Invalid data'

    if (( secret < user )); then
        printf '<\n'
    else
        printf '>=\n'
    fi
    iteration=$(( iteration + 1 ))
done

finish "$RE" 'Unexpected flow'
