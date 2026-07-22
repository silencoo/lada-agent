#!/usr/bin/env sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

uv lock --check
uv sync --locked --group dev
uv run --group dev pytest -q --basetemp .test-tmp-check -p no:cacheprovider
uv run --group dev ruff check .
uv run --group dev ruff format --check .
node --check src/lada_agent/static/app.js
uv build
