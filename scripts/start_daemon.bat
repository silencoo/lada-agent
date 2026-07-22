@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
title LADA视频监控程序 - 后台运行

echo 启动LADA视频文件监控程序(后台运行)...
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

REM 后台启动监控
start /B uv run lada-monitor

echo.
echo 程序已在后台启动。要停止程序,请关闭此窗口或使用任务管理器结束 lada-monitor 进程。
echo.
