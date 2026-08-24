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
echo AUDION SHUTTER ENCODER UPDATE
echo ================================================================
echo [INFO] This script updates Shutter Encoder only.
echo [INFO] Target version is fixed to 19.8.
echo [INFO] It will not move above 19.8.
echo.


winget upgrade --id PaulPacifico.ShutterEncoder -e --version 19.8 --source winget --accept-source-agreements --accept-package-agreements
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Shutter Encoder processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] Shutter Encoder upgrade was cancelled.
    goto :end
)

if "%RC%"=="-1978335136" (
    echo [INFO] No update available for Shutter Encoder.
    goto :end
)

if "%RC%"=="-1978335212" (
    echo [INFO] No update available or no installed match for Shutter Encoder.
    goto :end
)

echo [WARN] Shutter Encoder upgrade returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
