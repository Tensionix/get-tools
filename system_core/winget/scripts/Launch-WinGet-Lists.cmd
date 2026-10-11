@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "ROOT=%~1"
set "ACTION=%~2"
shift
shift

if "%ROOT%"=="" goto :usage
if "%ACTION%"=="" goto :usage

set "PS1=%ROOT%system_core\winget\scripts\Run-WinGet-From-Lists.ps1"

if not exist "%PS1%" (
    echo [ERROR] Script not found: "%PS1%"
    goto :fail
)

set "WINGET_LIST_1="
set "WINGET_LIST_2="
set "WINGET_LIST_3="
set "WINGET_LIST_4="
set /a LIST_COUNT=0

:collect
if "%~1"=="" goto :run
set /a LIST_COUNT+=1
if %LIST_COUNT%==1 set "WINGET_LIST_1=%~1"
if %LIST_COUNT%==2 set "WINGET_LIST_2=%~1"
if %LIST_COUNT%==3 set "WINGET_LIST_3=%~1"
if %LIST_COUNT%==4 set "WINGET_LIST_4=%~1"
shift
goto :collect

:run
set "WINGET_ACTION=%ACTION%"

call :run_pwsh
set "RC=%ERRORLEVEL%"
exit /b %RC%

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

:usage
echo [ERROR] Usage: Launch-WinGet-Lists.cmd ^<root^> ^<install^|update^|pin^> list1 [list2 ...]
exit /b 2

:fail
exit /b 1
