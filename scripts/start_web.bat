@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
pushd "%PROJECT_ROOT%"

echo 启动LADA监控程序Web管理界面...
echo.

where uv >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到uv，请先安装uv
    popd
    pause
    exit /b 1
)

uv sync
if errorlevel 1 (
    echo 依赖同步失败
    popd
    pause
    exit /b 1
)

echo 启动Web服务器...
uv run lada-web --host 127.0.0.1 --port 5000
set "EXIT_CODE=%ERRORLEVEL%"

popd
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
