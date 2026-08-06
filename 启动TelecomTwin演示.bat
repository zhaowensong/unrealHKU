@echo off
setlocal
chcp 65001 >nul
title TelecomTwin Demo Launcher

set "PROJECT_ROOT=%~dp0"
set "POWERSHELL_EXE=pwsh.exe"
where pwsh.exe >nul 2>nul
if errorlevel 1 set "POWERSHELL_EXE=powershell.exe"

"%POWERSHELL_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%Scripts\OpenMassCrowd\start_investor_delivery_demo.ps1" -Detached
set "DEMO_EXIT=%ERRORLEVEL%"

if not "%DEMO_EXIT%"=="0" (
    echo.
    echo TelecomTwin demo failed. Keep the error above for diagnosis.
    pause
    exit /b %DEMO_EXIT%
)

echo.
echo TelecomTwin automation is running. Unreal Editor will show DEMO READY.
exit /b 0
