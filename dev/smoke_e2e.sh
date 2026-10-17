#!/usr/bin/env bash
# Neutral mock CLI only; private paths and inherited CLI overrides are isolated.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/dev/smoke_e2e.py" "$@"
