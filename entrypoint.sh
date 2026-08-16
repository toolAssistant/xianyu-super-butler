#!/usr/bin/env sh
set -eu

mkdir -p /app/data /app/logs /app/backups /app/static/uploads/images

exec python Start.py
