@echo off
chcp 65001 >nul
title 视频修复工具

echo 视频修复工具
echo ================
echo.

if "%~1"=="" (
    echo 使用方法: fix_video.bat ^<视频文件路径^>
    echo 示例: fix_video.bat "C:\path\to\video.mp4"
    pause
    exit /b 1
)

set "input_file=%~1"
set "output_file=%~dpn1_fixed%~x1"

echo 输入文件: %input_file%
echo 输出文件: %output_file%
echo.

echo 正在修复视频时间戳...
ffmpeg -i "%input_file%" -c copy -avoid_negative_ts make_zero -fflags +genpts -y "%output_file%"

if %errorlevel% equ 0 (
    echo.
    echo ✅ 修复成功！
    echo 修复后的文件: %output_file%
) else (
    echo.
    echo ❌ 修复失败，尝试重新编码...
    ffmpeg -i "%input_file%" -c:v libx264 -c:a aac -preset fast -crf 23 -avoid_negative_ts make_zero -fflags +genpts -y "%output_file%"
    
    if %errorlevel% equ 0 (
        echo.
        echo ✅ 重新编码成功！
        echo 修复后的文件: %output_file%
    ) else (
        echo.
        echo ❌ 修复失败！
    )
)

echo.
pause
