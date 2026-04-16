#!/usr/bin/env bash
set -euo pipefail

echo "Running alembic migrations..."
# Check if alembic_version table exists (database already initialized)
if alembic current 2>/dev/null | grep -q "."; then
    echo "Database already initialized, running migrations..."
    alembic upgrade head
else
    # Database might have tables from manual migrations or be empty
    # Try to run migrations, if tables exist this will fail
    if ! alembic upgrade head 2>/dev/null; then
        echo "Tables already exist, stamping database with current version..."
        alembic stamp head
    fi
fi

echo "Starting command: $@"
exec "$@"