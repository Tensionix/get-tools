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
echo AUDION Node.js LTS UPDATE
echo ================================================================
echo [INFO] This script handles Node.js LTS only.
echo [INFO] It uses interactive update mode without --silent.
echo [INFO] If Node.js LTS is not installed, it will be skipped.
echo.


winget upgrade --id OpenJS.NodeJS.LTS -e --source winget --accept-source-agreements --accept-package-agreements
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Node.js LTS processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] Node.js LTS update was cancelled.
    goto :end
)

if "%RC%"=="-1978335136" (
    echo [INFO] No update available for Node.js LTS.
    goto :end
)

if "%RC%"=="-1978335189" (
    echo [INFO] No update available for Node.js LTS.
    goto :end
)

if "%RC%"=="-1978335212" (
    echo [INFO] No update available or no installed match for Node.js LTS.
    goto :end
)

echo [WARN] Node.js LTS update returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
