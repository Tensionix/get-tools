@echo off
setlocal EnableExtensions
chcp 65001 >nul

pushd "%~dp0..\..\.."
set "ROOT=%CD%\"
popd

set "PS1=%ROOT%system_core\winget\scripts\Remove-From-Lists.ps1"

if not exist "%PS1%" (
    echo [ERROR] Script not found: %PS1%
    if not defined AUDION_NO_PAUSE pause
    exit /b 1
)

call :run_pwsh
exit /b %ERRORLEVEL%

:run_pwsh
rem The PowerShell 7 the program carries, when the window is built; a PowerShell of the machine after it.
rem The host is chosen first and started outside the brackets: inside them cmd reads %ERRORLEVEL%
rem before the host runs, and the exit code of the script was lost.
set "PS_HOST=powershell.exe"
where pwsh >nul 2>nul
if not errorlevel 1 set "PS_HOST=pwsh"
if exist "E:\Audion\Tools\pwsh.exe" set "PS_HOST=E:\Audion\Tools\pwsh.exe"
if exist "S:\Audion\Tools\pwsh.exe" set "PS_HOST=S:\Audion\Tools\pwsh.exe"
if exist "%ROOT%App\AudionPs.exe" set "PS_HOST=%ROOT%App\AudionPs.exe"
"%PS_HOST%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
exit /b %ERRORLEVEL%
