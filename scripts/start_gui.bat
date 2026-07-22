@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
pushd "%PROJECT_ROOT%"

echo 启动LADA Agent桌面界面...

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

uv run lada-gui
set "EXIT_CODE=%ERRORLEVEL%"
popd

if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
