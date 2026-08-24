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
echo AUDION Node.js LTS INSTALL
echo ================================================================
echo [INFO] This script handles Node.js LTS only.
echo [INFO] It uses interactive install mode without --silent.
echo [INFO] If Node.js LTS is already installed, winget will keep the current version because --no-upgrade is used.
echo.


winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-package-agreements --accept-source-agreements --no-upgrade
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Node.js LTS processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] Node.js LTS installation was cancelled.
    goto :end
)

echo [WARN] Node.js LTS install returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
