@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

for %%A in ("%~dp0..") do set "ROOT=%%~fA\"

set "RUNTIME_DIR=%ROOT%._runtime"
set "MENU_FILE=%RUNTIME_DIR%\tools_menu_ru.txt"
set "RES_FILE=%RUNTIME_DIR%\tools_menu_ru_res.txt"

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
echo   Audion Get Tools - приложения
echo ================================================================
echo Режим меню: %MENU_MODE%
echo.

if defined FZF_CMD goto FZF_MENU
goto FALLBACK_MENU

:FZF_MENU
>  "%MENU_FILE%" echo [01] 📤 Экспортировать установленные приложения ^| export_apps    ^| экспорт WinGet в файл
>> "%MENU_FILE%" echo [02] 📥 Импортировать из файла экспорта         ^| import_apps    ^| импорт WinGet из файла
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [03] 🔎 Проверить отдельные приложения          ^| check_apps     ^| меню проверки
>> "%MENU_FILE%" echo [04] 📦 Установить отдельные приложения         ^| install_apps   ^| меню установки
>> "%MENU_FILE%" echo [05] 🔄 Обновить отдельные приложения           ^| update_apps    ^| меню обновления
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [06] 🔍 Поиск в WinGet                          ^| discover       ^| поиск в реестре WinGet
>> "%MENU_FILE%" echo [07] 🎯 Установить один пакет из списков        ^| install_single ^| точечная установка из всех тематических списков
>> "%MENU_FILE%" echo [08] 🎯 Обновить один пакет из списков          ^| update_single  ^| точечное обновление из всех тематических списков
>> "%MENU_FILE%" echo [09] 🗑 Удалить пакет из списков                 ^| remove_lists   ^| удаление из всех тематических списков
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [00] 🚪 Выход                                   ^| exit           ^| закрыть

"%FZF_CMD%" --bind="backspace:abort" --prompt="audion@winget-tools > " --pointer=">" --header="Выберите действие:" --layout=reverse --border=rounded --info=hidden --margin=1,2 < "%MENU_FILE%" > "%RES_FILE%"

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
echo([1] Экспортировать установленные приложения
echo([2] Импортировать из файла экспорта
echo([3] Проверить отдельные приложения
echo([4] Установить отдельные приложения
echo([5] Обновить отдельные приложения
echo([6] Поиск в WinGet
echo([7] Установить один пакет из списков
echo([8] Обновить один пакет из списков
echo([9] Удалить пакет из списков
echo([0] Выход
echo.
choice /C 1234567890 /N /M "Выбор: "
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
call "%ROOT%cli\Launcher-Audion-Check-Apps-RU.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_APPS
call "%ROOT%cli\Launcher-Audion-Install-Apps-RU.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_APPS
call "%ROOT%cli\Launcher-Audion-Update-Apps-RU.cmd"
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
echo([INFO] Процесс завершён с кодом %RC%.
echo([INFO] Нажмите любую клавишу, чтобы вернуться в меню инструментов.
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
