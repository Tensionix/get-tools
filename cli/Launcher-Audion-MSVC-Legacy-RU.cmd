@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

for %%A in ("%~dp0..") do set "ROOT=%%~fA\"
set "RUNTIME_DIR=%ROOT%._runtime"
set "MENU_FILE=%RUNTIME_DIR%\msvc_menu_ru.txt"
set "RES_FILE=%RUNTIME_DIR%\msvc_menu_ru_res.txt"

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
echo   Audion - рантаймы MSVC
echo ================================================================
echo Режим меню: %MENU_MODE%
echo.
echo Обновление legacy-версий намеренно отключено.
echo Установка использует два проверенных MSVC-скрипта; обновление только для 2015+.
echo.

if defined FZF_CMD goto FZF_MENU
goto FALLBACK_MENU

:FZF_MENU
>  "%MENU_FILE%" echo [01] 🧩 Установить MSVC 2015+ x86/x64    ^| install_2015   ^| Install-Audion-MSVC-2015+.cmd
>> "%MENU_FILE%" echo [02] 🧩 Установить MSVC Legacy 2005-2013 ^| install_legacy ^| Install-Audion-MSVC.cmd
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [03] 🔄 Обновить MSVC 2015+ x86/x64      ^| update_2015    ^| прямые установщики Microsoft
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [00] 🚪 Выход                            ^| exit           ^| закрыть

"%FZF_CMD%" --bind="backspace:abort" --prompt="audion@msvc > " --pointer=">" --header="Выберите действие MSVC:" --layout=reverse --border=rounded --info=hidden --margin=1,2 < "%MENU_FILE%" > "%RES_FILE%"

set "CHOICE="
set /p CHOICE=<"%RES_FILE%"
if not defined CHOICE exit /b 200

for /f "tokens=2 delims=|" %%a in ("%CHOICE%") do set "RAW=%%a"
call :TRIM RAW

if /I "%RAW%"=="install_2015"   goto INSTALL_2015
if /I "%RAW%"=="install_legacy" goto INSTALL_LEGACY
if /I "%RAW%"=="update_2015"    goto UPDATE_2015
if /I "%RAW%"=="exit"           goto END
goto MAIN

:FALLBACK_MENU
echo([1] Установить MSVC 2015+ x86/x64
echo([2] Установить MSVC Legacy 2005-2013
echo([3] Обновить MSVC 2015+ x86/x64
echo([0] Выход
echo.
choice /C 1230 /N /M "Выбор: "
if errorlevel 4 goto END
if errorlevel 3 goto UPDATE_2015
if errorlevel 2 goto INSTALL_LEGACY
if errorlevel 1 goto INSTALL_2015
goto MAIN

:INSTALL_2015
call :CONFIRM_RUN "Установить MSVC 2015+ x86/x64"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-Audion-MSVC-2015+.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_LEGACY
call :CONFIRM_RUN "Установить MSVC Legacy 2005-2013"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-Audion-MSVC.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_2015
call :CONFIRM_RUN "Обновить MSVC 2015+ x86/x64"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Update-Audion-MSVC-2015+.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:CONFIRM_RUN
echo.
set "ANSWER="
set /p "ANSWER=Запустить %~1? [Enter/Y] Да  [N] Нет  [Q] Выход: "
if not defined ANSWER exit /b 0
if /I "%ANSWER%"=="Y" exit /b 0
if /I "%ANSWER%"=="N" exit /b 1
if /I "%ANSWER%"=="Q" exit /b 2
goto CONFIRM_RUN

:RETURN_MAIN
echo.
echo([INFO] Процесс завершён с кодом %RC%.
echo([INFO] Нажмите любую клавишу, чтобы вернуться в меню MSVC.
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
