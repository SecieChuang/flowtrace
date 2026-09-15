#!/usr/bin/env sh
set -eu

container="${FLOWTRACE_CONTAINER:-flowtrace-server}"

docker exec -it "$container" python manage_cli.py "$@"
