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
echo AUDION WINDOWS TERMINAL UPDATE
echo ================================================================
echo [INFO] This script updates Windows Terminal only.
echo [INFO] If Windows Terminal is not installed, it will be skipped.
echo.


winget upgrade --id Microsoft.WindowsTerminal -e --source winget --accept-source-agreements --accept-package-agreements
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Windows Terminal processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] Windows Terminal update was cancelled.
    goto :end
)

if "%RC%"=="-1978335136" (
    echo [INFO] No update available for Windows Terminal.
    goto :end
)

if "%RC%"=="-1978335189" (
    echo [INFO] No update available for Windows Terminal.
    goto :end
)

if "%RC%"=="-1978335212" (
    echo [INFO] No update available or no installed match for Windows Terminal.
    goto :end
)

echo [WARN] Windows Terminal update returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
