#!/usr/bin/env bash
set -euo pipefail

echo "Running alembic migrations..."
alembic upgrade head

echo "Starting command: $@"
exec "$@"