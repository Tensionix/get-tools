@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

for %%A in ("%~dp0..") do set "ROOT=%%~fA\"

set "RUNTIME_DIR=%ROOT%._runtime"
set "MENU_FILE=%RUNTIME_DIR%\checkapps_menu_ru.txt"
set "RES_FILE=%RUNTIME_DIR%\checkapps_menu_ru_res.txt"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%" >nul 2>nul
del /q "%MENU_FILE%" "%RES_FILE%" >nul 2>nul

call :RESOLVE_FZF
if errorlevel 1 (
    set "MENU_MODE=резервное CMD-меню"
) else (
    set "MENU_MODE=FZF"
)
if /I "%AUDION_DISABLE_FZF%"=="1" (
    set "FZF_CMD="
    set "MENU_MODE=резервное CMD-меню"
)

:MAIN
cls
echo ================================================================
echo   Audion - проверка приложений
echo ================================================================
echo Режим меню: %MENU_MODE%
echo.

if defined FZF_CMD goto FZF_MENU
goto FALLBACK_MENU

:FZF_MENU
>  "%MENU_FILE%" echo [01] Проверить Docker Desktop  ^| check_docker   ^| winget list Docker.DockerDesktop
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [02] Проверить MSVC 2015+ x64  ^| check_msvc_x64 ^| winget list Microsoft.VCRedist.2015+.x64
>> "%MENU_FILE%" echo [03] Проверить MSVC 2015+ x86  ^| check_msvc_x86 ^| winget list Microsoft.VCRedist.2015+.x86
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [04] Проверить Node.js LTS     ^| check_nodejs   ^| winget list OpenJS.NodeJS.LTS
>> "%MENU_FILE%" echo [05] Проверить Obsidian        ^| check_obsidian ^| winget list Obsidian.Obsidian
>> "%MENU_FILE%" echo [06] Проверить Python 3.12     ^| check_python   ^| winget list Python.Python.3.12
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [07] Проверить Shutter Encoder ^| check_shutter  ^| winget list PaulPacifico.ShutterEncoder
>> "%MENU_FILE%" echo [08] Проверить LosslessCut     ^| check_lossless ^| winget list mifi.losslesscut
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [00] 🚪 Выход                  ^| exit           ^| закрыть

"%FZF_CMD%" --bind="backspace:abort" --prompt="audion@check-apps > " --pointer=">" --header="Выберите приложение для проверки:" --layout=reverse --border=rounded --info=hidden --margin=1,2 < "%MENU_FILE%" > "%RES_FILE%"

set "CHOICE="
set /p CHOICE=<"%RES_FILE%"
if not defined CHOICE exit /b 200

for /f "tokens=2 delims=|" %%a in ("%CHOICE%") do set "RAW=%%a"
call :TRIM RAW

if /I "%RAW%"=="check_docker"   goto CHECK_DOCKER
if /I "%RAW%"=="check_msvc_x64" goto CHECK_MSVC_X64
if /I "%RAW%"=="check_msvc_x86" goto CHECK_MSVC_X86
if /I "%RAW%"=="check_nodejs"   goto CHECK_NODEJS
if /I "%RAW%"=="check_obsidian" goto CHECK_OBSIDIAN
if /I "%RAW%"=="check_python"   goto CHECK_PYTHON
if /I "%RAW%"=="check_shutter"  goto CHECK_SHUTTER
if /I "%RAW%"=="check_lossless" goto CHECK_LOSSLESS
if /I "%RAW%"=="exit"           goto END
goto MAIN

:FALLBACK_MENU
echo([1] Проверить Docker Desktop
echo([2] Проверить MSVC 2015+ x64
echo([3] Проверить MSVC 2015+ x86
echo([4] Проверить Node.js LTS
echo([5] Проверить Obsidian
echo([6] Проверить Python 3.12
echo([7] Проверить Shutter Encoder
echo([8] Проверить LosslessCut
echo([0] Выход
echo.
choice /C 123456780 /N /M "Выбор: "
if errorlevel 9 goto END
if errorlevel 8 goto CHECK_LOSSLESS
if errorlevel 7 goto CHECK_SHUTTER
if errorlevel 6 goto CHECK_PYTHON
if errorlevel 5 goto CHECK_OBSIDIAN
if errorlevel 4 goto CHECK_NODEJS
if errorlevel 3 goto CHECK_MSVC_X86
if errorlevel 2 goto CHECK_MSVC_X64
if errorlevel 1 goto CHECK_DOCKER
goto MAIN

:CHECK_DOCKER
call "%ROOT%system_core\winget\check_apps\Check-Audion-DockerDesktop.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:CHECK_MSVC_X64
call "%ROOT%system_core\winget\check_apps\Check-Audion-MSVC-2015+_x64.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:CHECK_MSVC_X86
call "%ROOT%system_core\winget\check_apps\Check-Audion-MSVC-2015+_x86.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:CHECK_NODEJS
call "%ROOT%system_core\winget\check_apps\Check-Audion-NodeJS-LTS.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:CHECK_OBSIDIAN
call "%ROOT%system_core\winget\check_apps\Check-Audion-Obsidian.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:CHECK_PYTHON
call "%ROOT%system_core\winget\check_apps\Check-Audion-Python-3.12.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:CHECK_SHUTTER
call "%ROOT%system_core\winget\check_apps\Check-Audion-ShutterEncoder.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:CHECK_LOSSLESS
call "%ROOT%system_core\winget\check_apps\Check-Audion-LosslessCut.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:RETURN_MAIN
echo.
echo([INFO] Процесс завершён с кодом %RC%.
echo([INFO] Нажмите любую клавишу, чтобы вернуться в меню проверки.
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
