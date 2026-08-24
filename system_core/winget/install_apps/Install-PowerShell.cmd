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
echo AUDION POWERSHELL INSTALL
echo ================================================================
echo [INFO] This script installs PowerShell 7+ only.
echo [INFO] Windows PowerShell 5.x does not count as PowerShell 7.
echo.

where pwsh >nul 2>nul
if not errorlevel 1 (
    echo [INFO] PowerShell 7+ is already installed. Skipping.
    goto :end
)

winget install --id Microsoft.PowerShell -e --source winget --accept-source-agreements --accept-package-agreements --no-upgrade
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] PowerShell processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] PowerShell installation was cancelled.
    goto :end
)

echo [WARN] PowerShell install returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
