#!/usr/bin/env sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

if ! command -v uv >/dev/null 2>&1; then
    echo "错误: 未找到 uv，请先安装 https://docs.astral.sh/uv/" >&2
    exit 1
fi

uv sync --locked

if [ ! -f "config/config.json" ]; then
    uv run lada-monitor --init-config
    echo "已创建 config/config.json，请先填写监控目录和 Lada CLI 路径。"
    exit 0
fi

HOST=${LADA_AGENT_HOST:-127.0.0.1}
PORT=${LADA_AGENT_PORT:-5000}
exec uv run lada-web --host "$HOST" --port "$PORT"
