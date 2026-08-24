@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Messages and comments are intentionally kept in English.

echo ================================================================
echo AUDION PACKAGE CHECK
echo ================================================================
echo [INFO] Package: Node.js LTS
echo [INFO] ID:      OpenJS.NodeJS.LTS
echo.

where winget >nul 2>nul
if errorlevel 1 (
    echo [ERROR] winget was not found in PATH.
    goto :end
)

echo ----------------------------------------------------------------
echo [INFO] Exact match in winget source:
winget list --id OpenJS.NodeJS.LTS -e --source winget

echo.
echo ----------------------------------------------------------------
echo [INFO] Fallback exact match without source filter:
winget list --id OpenJS.NodeJS.LTS -e

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal
