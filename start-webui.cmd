@echo off
setlocal
title nanobot WebUI

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-webui.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo nanobot WebUI failed with exit code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
