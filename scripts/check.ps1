$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    uv lock --check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv sync --locked --group dev
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv run --group dev pytest -q --basetemp .test-tmp-check -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv run --group dev ruff check .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv run --group dev ruff format --check .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    node --check src/lada_agent/static/app.js
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
