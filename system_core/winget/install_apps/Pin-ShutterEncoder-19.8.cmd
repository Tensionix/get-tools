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
echo AUDION SHUTTER ENCODER PIN
echo ================================================================
echo [INFO] This script pins Shutter Encoder to version 19.8 only.
echo [INFO] A pinned package can still be changed outside winget.
echo.


winget pin add --id PaulPacifico.ShutterEncoder --version 19.8 --source winget --accept-source-agreements
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Shutter Encoder pinned to 19.8.
    goto :end
)

if "%RC%"=="-1978335155" (
    echo [INFO] Shutter Encoder is already pinned to the requested version.
    goto :end
)

if "%RC%"=="-1978335212" (
    echo [INFO] Shutter Encoder is not installed or no installed match was found.
    goto :end
)

echo [WARN] Pin command returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
