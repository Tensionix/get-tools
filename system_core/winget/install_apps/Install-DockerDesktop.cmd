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
echo AUDION Docker Desktop INSTALL
echo ================================================================
echo [INFO] This script handles Docker Desktop only.
echo [INFO] It uses interactive install mode without --silent.
echo [INFO] If Docker Desktop is already installed, winget will keep the current version because --no-upgrade is used.
echo.


winget install --id Docker.DockerDesktop -e --source winget --accept-package-agreements --accept-source-agreements --no-upgrade
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Docker Desktop processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] Docker Desktop installation was cancelled.
    goto :end
)

echo [WARN] Docker Desktop install returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
