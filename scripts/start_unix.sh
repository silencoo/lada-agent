#!/usr/bin/env sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

echo "启动 LADA 视频文件监控程序（前台）..."

if ! command -v uv >/dev/null 2>&1; then
    echo "错误: 未找到 uv，请先安装 https://docs.astral.sh/uv/" >&2
    exit 1
fi

uv sync --locked

if [ ! -f "config/config.json" ]; then
    uv run lada-monitor --init-config
    echo "已创建 config/config.json，请填写监控目录和 LADA CLI 路径后重新运行。"
    exit 0
fi

exec uv run lada-monitor
