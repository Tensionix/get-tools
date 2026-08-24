@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Messages and comments are intentionally kept in English.

where winget >nul 2>nul
if errorlevel 1 (
    echo [ERROR] winget was not found in PATH.
    echo [INFO] Install or repair Microsoft App Installer first.
    goto :end
)

echo ================================================================
echo AUDION GET TOOLS SELF-UPDATE
echo ================================================================
echo [INFO] This script updates Microsoft App Installer, which provides winget.
echo.

winget upgrade --id Microsoft.AppInstaller -e --accept-source-agreements --accept-package-agreements
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo [OK] Microsoft App Installer processed successfully.
    goto :end
)

if "%RC%"=="2" (
    echo [CANCELLED] Microsoft App Installer update was cancelled.
    goto :end
)

if "%RC%"=="-1978335189" (
    echo [INFO] No update available for Microsoft App Installer.
    goto :end
)

if "%RC%"=="-1978335212" (
    echo [INFO] No installed match found for Microsoft App Installer.
    echo [INFO] Try Microsoft Store repair/update if winget itself is damaged.
    goto :end
)

echo [WARN] Microsoft App Installer update returned errorlevel %RC%.

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal
