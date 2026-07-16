#!/usr/bin/env bash

read -r upper
lower=1

while [ "$lower" -lt "$upper" ]; do
    number=$(( lower + (upper - lower) / 2 + 1 ))
    echo "$number"

    read -r answer
    answer="${answer#"${answer%%[![:space:]]*}"}"
    answer="${answer%"${answer##*[![:space:]]}"}"

    if [ "$answer" = "<" ]; then
        upper=$(( number - 1 ))
    elif [ "$answer" = ">=" ]; then
        lower=$number
    else
        echo "Invalid answer from validator: '$answer'" >&2
        exit 1
    fi
done

echo "!$lower"
exit 0
