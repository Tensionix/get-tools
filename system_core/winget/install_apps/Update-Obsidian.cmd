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
echo AUDION OBSIDIAN UPDATE
echo ================================================================
echo [INFO] This script updates Obsidian only.
echo [INFO] It uses interactive upgrade mode without --silent.
echo.


winget upgrade --id Obsidian.Obsidian -e --source winget --accept-source-agreements --accept-package-agreements
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Obsidian processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] Obsidian upgrade was cancelled.
    goto :end
)

if "%RC%"=="-1978335136" (
    echo [INFO] No update available for Obsidian.
    goto :end
)

if "%RC%"=="-1978335189" (
    echo [INFO] No update available for Obsidian.
    goto :end
)

if "%RC%"=="-1978335212" (
    echo [INFO] No update available or no installed match for Obsidian.
    goto :end
)

echo [WARN] Obsidian upgrade returned errorlevel %RC%.
set "EXIT_RC=%RC%"

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
