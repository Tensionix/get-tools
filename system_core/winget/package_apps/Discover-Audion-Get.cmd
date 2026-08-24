@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo ================================================================
echo AUDION GET TOOLS DISCOVER
echo ================================================================
echo.
set /p QUERY=Enter package name to search:

if "%QUERY%"=="" (
    echo [ERROR] No query entered.
    goto :end
)

echo.
echo [INFO] Searching winget for: %QUERY%
echo.

winget search --source winget --accept-source-agreements --disable-interactivity %QUERY%

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause >nul
endlocal
