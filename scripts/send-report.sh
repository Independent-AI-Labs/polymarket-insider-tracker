#!/usr/bin/env bash
# Thin wrapper around send-report.py — runs inside the project venv
# Usage: ./scripts/send-report.sh [args...]
exec uv run python3 "$(dirname "$0")/send-report.py" "$@"
