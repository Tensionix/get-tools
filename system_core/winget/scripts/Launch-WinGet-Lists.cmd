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

:usage
echo [ERROR] Usage: Launch-WinGet-Lists.cmd ^<root^> ^<install^|update^|pin^> list1 [list2 ...]
exit /b 2

:fail
exit /b 1
