@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
title LADA视频监控程序

echo 启动LADA视频文件监控程序(前台)...
echo.

REM 检查 uv
where uv >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到 uv,请先安装 https://github.com/astral-sh/uv
    pause
    exit /b 1
)

REM 确保依赖已安装
uv sync >nul 2>&1

REM 启动监控
uv run lada-monitor

echo.
echo 程序已停止运行
pause
