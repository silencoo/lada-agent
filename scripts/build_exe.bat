@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
pushd "%PROJECT_ROOT%"

echo 构建LADA Agent Windows可执行文件...
echo.

where uv >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到uv，请先安装uv
    popd
    pause
    exit /b 1
)

REM PyInstaller is installed in an isolated uv environment for this build only.
uv run --with pyinstaller pyinstaller --clean "packaging\LadaAgent.spec"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo 构建失败
    popd
    pause
    exit /b %EXIT_CODE%
)

echo.
echo 构建成功: dist\LadaAgent.exe
echo 请将config目录和cli目录与可执行文件一起分发。
popd
pause
exit /b 0
