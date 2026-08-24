@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "EXIT_RC=0"

rem Messages and comments are intentionally kept in English.

where winget >nul 2>nul
if errorlevel 1 (
    echo [ERROR] winget was not found in PATH.
    echo [INFO] Update App Installer from Microsoft Store and try again.
    set "EXIT_RC=1"
    goto :end
)

echo ================================================================
echo AUDION WINDOWS TERMINAL INSTALL
echo ================================================================
echo [INFO] This script installs Windows Terminal only.
echo [INFO] If Windows Terminal is already installed, winget will keep the current version because --no-upgrade is used.
echo.


winget install --id Microsoft.WindowsTerminal -e --source winget --accept-source-agreements --accept-package-agreements --no-upgrade
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Windows Terminal processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] Windows Terminal installation was cancelled.
    goto :end
)

echo [WARN] Windows Terminal install returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
