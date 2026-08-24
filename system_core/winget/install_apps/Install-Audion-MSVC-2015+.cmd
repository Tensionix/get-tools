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
echo AUDION MSVC 2015+ INSTALL
echo ================================================================
echo [INFO] This script installs MSVC 2015+ runtimes only (x86 and x64).
echo [INFO] Existing runtimes are left as-is because winget install is used with --no-upgrade.
echo.

call :install "Microsoft.VCRedist.2015+.x86" "Microsoft Visual C++ 2015-2022 Redistributable (x86)"
call :REMEMBER_STEP
call :install "Microsoft.VCRedist.2015+.x64" "Microsoft Visual C++ 2015-2022 Redistributable (x64)"
call :REMEMBER_STEP

echo.
echo [INFO] Done.
goto :end

:install
echo.
echo ----------------------------------------------------------------
echo [INFO] Installing %~2
echo [INFO] ID: %~1

winget install --id %~1 -e --source winget --accept-source-agreements --accept-package-agreements --no-upgrade
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" (
    echo [OK] Installed %~1
    exit /b 0
)
if "%RC%"=="2" (
    echo [CANCELLED] %~1
    exit /b 0
)
echo [WARN] Installation returned errorlevel %RC% for %~1
exit /b %RC%

:REMEMBER_STEP
set "STEP_RC=%ERRORLEVEL%"
if not "%STEP_RC%"=="0" if "%EXIT_RC%"=="0" set "EXIT_RC=%STEP_RC%"
exit /b 0

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
