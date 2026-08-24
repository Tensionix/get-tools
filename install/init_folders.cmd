@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

for %%A in ("%SCRIPT_DIR%") do set "HERE=%%~nxA"

set "ROOT=%SCRIPT_DIR%"
if /I "%HERE%"=="install" for %%A in ("%SCRIPT_DIR%\..") do set "ROOT=%%~fA"

call :MK "%ROOT%\logs"
call :MK "%ROOT%\._runtime"
call :MK "%ROOT%\input"
call :MK "%ROOT%\output"
call :MK "%ROOT%\report"
call :MK "%ROOT%\workspace"
call :MK "%ROOT%\release"
call :MK "%ROOT%\runtime"
call :MK "%ROOT%\wheelhouse"
call :MK "%ROOT%\system_core"
call :MK "%ROOT%\system_core\winget"
call :MK "%ROOT%\system_core\winget\package_apps"
call :MK "%ROOT%\system_core\winget\scripts"
call :MK "%ROOT%\system_core\winget\check_apps"
call :MK "%ROOT%\system_core\winget\install_apps"
call :MK "%ROOT%\system_core\winget\export_import"
call :MK "%ROOT%\system_core\winget\msvc_legacy_updates"
call :MK "%ROOT%\system_core\powershell"
call :MK "%ROOT%\install"
call :MK "%ROOT%\install\download"
call :MK "%ROOT%\licenses"
call :MK "%ROOT%\config"

call :KEEP "%ROOT%\logs"
call :KEEP "%ROOT%\._runtime"
call :KEEP "%ROOT%\report"
call :KEEP "%ROOT%\workspace"
call :KEEP "%ROOT%\release"
call :KEEP "%ROOT%\runtime"
call :KEEP "%ROOT%\wheelhouse"
call :KEEP "%ROOT%\system_core\winget"
call :KEEP "%ROOT%\system_core\powershell"
call :KEEP "%ROOT%\install\download"
call :KEEP "%ROOT%\licenses"

exit /b 0

:MK
if not exist "%~1\" mkdir "%~1" >nul 2>nul
goto :eof

:KEEP
goto :eof
