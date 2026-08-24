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
echo AUDION Python 3.12 INSTALL
echo ================================================================
echo [INFO] This script handles Python 3.12 only.
echo [INFO] It uses interactive install mode without --silent.
echo [INFO] If Python 3.12 is already installed, winget will keep the current version because --no-upgrade is used.
echo.


winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements --no-upgrade
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Python 3.12 processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] Python 3.12 installation was cancelled.
    goto :end
)

echo [WARN] Python 3.12 install returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
