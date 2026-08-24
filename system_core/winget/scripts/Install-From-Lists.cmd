@echo off
setlocal EnableExtensions
chcp 65001 >nul

pushd "%~dp0..\..\.."
set "ROOT=%CD%\"
popd

set "PS1=%ROOT%system_core\winget\scripts\Install-From-Lists.ps1"

if not exist "%PS1%" (
    echo [ERROR] Script not found: %PS1%
    if not defined AUDION_NO_PAUSE pause
    exit /b 1
)

call :run_pwsh
exit /b %ERRORLEVEL%

:run_pwsh
if exist "%ROOT%system_core\powershell\pwsh.exe" (
    "%ROOT%system_core\powershell\pwsh.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
    exit /b %ERRORLEVEL%
)
if exist "S:\Audion\Tools\pwsh.exe" (
    "S:\Audion\Tools\pwsh.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
    exit /b %ERRORLEVEL%
)
if exist "E:\Audion\Tools\pwsh.exe" (
    "E:\Audion\Tools\pwsh.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
    exit /b %ERRORLEVEL%
)
where pwsh >nul 2>nul
if not errorlevel 1 (
    pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
    exit /b %ERRORLEVEL%
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
exit /b %ERRORLEVEL%
