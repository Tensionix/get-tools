@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

for %%A in ("%~dp0..") do set "ROOT=%%~fA\"

set "RUNTIME_DIR=%ROOT%._runtime"
set "MENU_FILE=%RUNTIME_DIR%\tools_menu.txt"
set "RES_FILE=%RUNTIME_DIR%\tools_menu_res.txt"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%" >nul 2>nul
del /q "%MENU_FILE%" "%RES_FILE%" >nul 2>nul

call :RESOLVE_FZF
if errorlevel 1 (
    set "MENU_MODE=CMD fallback"
) else (
    set "MENU_MODE=FZF"
)
if /I "%AUDION_DISABLE_FZF%"=="1" (
    set "FZF_CMD="
    set "MENU_MODE=CMD fallback"
)

:MAIN
cls
echo ================================================================
echo   Audion Get Tools - Apps
echo ================================================================
echo Menu mode: %MENU_MODE%
echo.

if defined FZF_CMD goto FZF_MENU
goto FALLBACK_MENU

:FZF_MENU
>  "%MENU_FILE%" echo [01] 📤 Export installed apps     ^| export_apps    ^| winget export to file
>> "%MENU_FILE%" echo [02] 📥 Import from export file   ^| import_apps    ^| winget import from file
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [03] 🔎 Check individual apps     ^| check_apps     ^| check sub-menu
>> "%MENU_FILE%" echo [04] 📦 Install individual apps   ^| install_apps   ^| install sub-menu
>> "%MENU_FILE%" echo [05] 🔄 Update individual apps    ^| update_apps    ^| update sub-menu
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [06] 🔍 Discover - search WinGet  ^| discover       ^| winget search registry
>> "%MENU_FILE%" echo [07] 🎯 Install single from lists ^| install_single ^| point install from all thematic lists
>> "%MENU_FILE%" echo [08] 🎯 Update single from lists  ^| update_single  ^| point update from all thematic lists
>> "%MENU_FILE%" echo [09] 🗑 Remove package from lists  ^| remove_lists   ^| remove from all thematic lists
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [00] 🚪 Exit                      ^| exit           ^| close

"%FZF_CMD%" --bind="backspace:abort" --prompt="audion@winget-tools > " --pointer=">" --header="Choose an action:" --layout=reverse --border=rounded --info=hidden --margin=1,2 < "%MENU_FILE%" > "%RES_FILE%"

set "CHOICE="
set /p CHOICE=<"%RES_FILE%"
if not defined CHOICE exit /b 200

for /f "tokens=2 delims=|" %%a in ("%CHOICE%") do set "RAW=%%a"
call :TRIM RAW

if /I "%RAW%"=="export_apps"  goto EXPORT_APPS
if /I "%RAW%"=="import_apps"  goto IMPORT_APPS
if /I "%RAW%"=="check_apps"   goto CHECK_APPS
if /I "%RAW%"=="install_apps" goto INSTALL_APPS
if /I "%RAW%"=="update_apps"  goto UPDATE_APPS
if /I "%RAW%"=="discover"     goto DISCOVER
if /I "%RAW%"=="install_single" goto INSTALL_SINGLE
if /I "%RAW%"=="update_single"  goto UPDATE_SINGLE
if /I "%RAW%"=="remove_lists" goto REMOVE_LISTS
if /I "%RAW%"=="exit"         goto END
goto MAIN

:FALLBACK_MENU
echo [1] Export installed apps
echo [2] Import from export file
echo [3] Check individual apps
echo [4] Install individual apps
echo [5] Update individual apps
echo [6] Discover - search WinGet
echo [7] Install single from lists
echo [8] Update single from lists
echo [9] Remove package from lists
echo [0] Exit
echo.
choice /C 1234567890 /N /M "Select: "
if errorlevel 10 goto END
if errorlevel 9 goto REMOVE_LISTS
if errorlevel 8 goto UPDATE_SINGLE
if errorlevel 7 goto INSTALL_SINGLE
if errorlevel 6 goto DISCOVER
if errorlevel 5 goto UPDATE_APPS
if errorlevel 4 goto INSTALL_APPS
if errorlevel 3 goto CHECK_APPS
if errorlevel 2 goto IMPORT_APPS
if errorlevel 1 goto EXPORT_APPS
goto MAIN

:EXPORT_APPS
call "%ROOT%system_core\winget\export_import\Export-Audion-Get.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:IMPORT_APPS
call "%ROOT%system_core\winget\export_import\Import-Audion-Get.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:CHECK_APPS
call "%ROOT%cli\Launcher-Audion-Check-Apps.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_APPS
call "%ROOT%cli\Launcher-Audion-Install-Apps.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_APPS
call "%ROOT%cli\Launcher-Audion-Update-Apps.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:DISCOVER
call "%ROOT%system_core\winget\package_apps\Discover-Audion-Get.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_SINGLE
call "%ROOT%system_core\winget\scripts\Install-From-Lists.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_SINGLE
call "%ROOT%system_core\winget\scripts\Update-From-Lists.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:REMOVE_LISTS
call "%ROOT%system_core\winget\scripts\Remove-From-Lists.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:RETURN_MAIN
if "%RC%"=="200" goto MAIN
echo.
echo [INFO] Process finished with exit code %RC%.
echo [INFO] Press any key to return to the tools menu.
if not defined AUDION_NO_PAUSE pause >nul
goto MAIN

:END
exit /b 0

:RESOLVE_FZF
set "FZF_CMD="
if exist "%ROOT%system_core\fzf.exe" (
    set "FZF_CMD=%ROOT%system_core\fzf.exe"
    exit /b 0
)
where fzf >nul 2>nul
if not errorlevel 1 (
    set "FZF_CMD=fzf"
    exit /b 0
)
exit /b 1

:TRIM
for /f "tokens=* delims= " %%z in ("!%~1!") do set "%~1=%%z"
:TRIM_R
if "!%~1:~-1!"==" " set "%~1=!%~1:~0,-1!" & goto TRIM_R
goto :eof
