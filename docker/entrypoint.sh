#!/bin/sh
set -eu

exec uvicorn app.main:app \
  --app-dir /opt/moss/backend \
  --host "${MOSS_HOST:-127.0.0.1}" \
  --port "${MOSS_PORT:-8000}"
