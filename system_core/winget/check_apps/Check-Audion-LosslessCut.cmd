@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Messages and comments are intentionally kept in English.

echo ================================================================
echo AUDION PACKAGE CHECK
echo ================================================================
echo [INFO] Package: LosslessCut
echo [INFO] ID:      ch.LosslessCut
echo.

where winget >nul 2>nul
if errorlevel 1 (
    echo [ERROR] winget was not found in PATH.
    goto :end
)

echo ----------------------------------------------------------------
echo [INFO] Exact match in winget source:
winget list --id ch.LosslessCut -e --source winget

echo.
echo ----------------------------------------------------------------
echo [INFO] Fallback exact match without source filter:
winget list --id ch.LosslessCut -e

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal
