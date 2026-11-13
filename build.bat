@echo off
REM Double-click this file to build. It bypasses the PowerShell execution policy.
REM ASCII only: cmd.exe reads .bat files using the OEM code page.
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
if errorlevel 1 (
    echo.
    echo BUILD FAILED - see the error above.
) else (
    echo.
    echo BUILD OK - dist\MusicUnlock\MusicUnlock.exe
)
pause
