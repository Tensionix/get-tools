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
echo AUDION SHUTTER ENCODER 19.8 INSTALL
echo ================================================================
echo [INFO] This script installs Shutter Encoder 19.8 only.
echo [INFO] It uses a fixed package ID and fixed version.
echo [INFO] It does not upgrade to newer versions.
echo.

echo [INFO] Installing Shutter Encoder 19.8
winget install --id PaulPacifico.ShutterEncoder -e --version 19.8 --source winget --accept-package-agreements --accept-source-agreements --no-upgrade
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Installed PaulPacifico.ShutterEncoder 19.8
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] PaulPacifico.ShutterEncoder
    goto :end
)

echo [WARN] Installation returned errorlevel %RC% for PaulPacifico.ShutterEncoder
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
