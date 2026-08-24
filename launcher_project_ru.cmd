@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "ROOT=%~dp0"
set "RUNTIME_DIR=%ROOT%._runtime"
set "MENU_FILE=%RUNTIME_DIR%\winget_menu_ru.txt"
set "RES_FILE=%RUNTIME_DIR%\winget_menu_ru_res.txt"

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
echo   Audion Get Tools - главное меню
echo ================================================================
echo Корень:      %ROOT%
echo Режим меню: %MENU_MODE%
echo Правило:    подтверждение Y/N/Q, Enter = Да
echo.

if defined FZF_CMD goto FZF_MENU
goto FALLBACK_MENU

:FZF_MENU
>  "%MENU_FILE%" echo [00] 🔄 Обновить сам WinGet              ^| wg_update      ^| Microsoft App Installer
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [01] 📦 Установить системный список      ^| install_system ^| рантаймы, терминалы, архиваторы
>> "%MENU_FILE%" echo [02] 🛠 Установить Dev-список             ^| install_dev    ^| инструменты разработки
>> "%MENU_FILE%" echo [03] 🧠 Установить AI-список             ^| install_ai     ^| AI-инструменты
>> "%MENU_FILE%" echo [04] 🗂 Установить PKMS-список            ^| install_pkms   ^| заметки и базы знаний
>> "%MENU_FILE%" echo [05] 📄 Установить Office/документы      ^| install_office ^| офис, документы, чтение
>> "%MENU_FILE%" echo [06] 🎬 Установить медиа-список          ^| install_media  ^| изображения, звук, видео
>> "%MENU_FILE%" echo [07] 🌐 Установить браузеры/VPN          ^| install_net    ^| браузеры, VPN, сетевые клиенты
>> "%MENU_FILE%" echo [08] 🧪 Установить железо/бенчмарки      ^| install_hw     ^| диагностика и бенчмарки
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [09] 🔄 Обновить системный список        ^| update_system  ^| рантаймы, терминалы, архиваторы
>> "%MENU_FILE%" echo [10] 🔄 Обновить Dev-список              ^| update_dev     ^| инструменты разработки
>> "%MENU_FILE%" echo [11] 🔄 Обновить AI-список               ^| update_ai      ^| AI-инструменты
>> "%MENU_FILE%" echo [12] 🔄 Обновить PKMS-список             ^| update_pkms    ^| заметки и базы знаний
>> "%MENU_FILE%" echo [13] 🔄 Обновить Office/документы        ^| update_office  ^| офис, документы, чтение
>> "%MENU_FILE%" echo [14] 🔄 Обновить медиа-список            ^| update_media   ^| изображения, звук, видео
>> "%MENU_FILE%" echo [15] 🔄 Обновить браузеры/VPN            ^| update_net     ^| браузеры, VPN, сетевые клиенты
>> "%MENU_FILE%" echo [16] 🔄 Обновить железо/бенчмарки        ^| update_hw      ^| диагностика и бенчмарки
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [17] 📌 Применить пины                   ^| pin            ^| config\pins.txt
>> "%MENU_FILE%" echo [18] 🧩 Рантаймы MSVC                    ^| msvc           ^| установка Legacy/2015+, обновление 2015+
>> "%MENU_FILE%" echo [19] 🧰 Инструменты                      ^| tools          ^| экспорт/импорт/проверка/точечные операции
>> "%MENU_FILE%" echo [20] 🎯 Установить один пакет из списков ^| install_single ^| точечная установка из всех тематических списков
>> "%MENU_FILE%" echo [21] 🎯 Обновить один пакет из списков   ^| update_single  ^| точечное обновление из всех тематических списков
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [99] 🚪 Выход                            ^| exit           ^| закрыть

"%FZF_CMD%" --bind="backspace:abort" --prompt="audion@winget > " --pointer=">" --header="Выберите действие:" --layout=reverse --border=rounded --info=hidden --margin=1,2 < "%MENU_FILE%" > "%RES_FILE%"

set "CHOICE="
set /p CHOICE=<"%RES_FILE%"
if not defined CHOICE goto END

for /f "tokens=2 delims=|" %%a in ("%CHOICE%") do set "RAW=%%a"
call :TRIM RAW

if /I "%RAW%"=="wg_update"      goto WG_UPDATE
if /I "%RAW%"=="install_system" goto INSTALL_SYSTEM
if /I "%RAW%"=="update_system"  goto UPDATE_SYSTEM
if /I "%RAW%"=="install_dev"    goto INSTALL_DEV
if /I "%RAW%"=="update_dev"     goto UPDATE_DEV
if /I "%RAW%"=="install_ai"     goto INSTALL_AI
if /I "%RAW%"=="update_ai"      goto UPDATE_AI
if /I "%RAW%"=="install_pkms"   goto INSTALL_PKMS
if /I "%RAW%"=="update_pkms"    goto UPDATE_PKMS
if /I "%RAW%"=="install_office" goto INSTALL_OFFICE
if /I "%RAW%"=="update_office"  goto UPDATE_OFFICE
if /I "%RAW%"=="install_media"  goto INSTALL_MEDIA
if /I "%RAW%"=="update_media"   goto UPDATE_MEDIA
if /I "%RAW%"=="install_net"    goto INSTALL_NET
if /I "%RAW%"=="update_net"     goto UPDATE_NET
if /I "%RAW%"=="install_hw"     goto INSTALL_HW
if /I "%RAW%"=="update_hw"      goto UPDATE_HW
if /I "%RAW%"=="pin"            goto PIN
if /I "%RAW%"=="msvc"           goto MSVC
if /I "%RAW%"=="tools"          goto Инструменты
if /I "%RAW%"=="install_single" goto INSTALL_SINGLE
if /I "%RAW%"=="update_single"  goto UPDATE_SINGLE
if /I "%RAW%"=="exit"           goto END
goto MAIN

:FALLBACK_MENU
echo([0]  Обновить сам WinGet
echo([1]  Установить системный список
echo([2]  Обновить системный список
echo([3]  Установить Dev-список
echo([4]  Обновить Dev-список
echo([5]  Установить AI-список
echo([6]  Обновить AI-список
echo([7]  Установить PKMS-список
echo([8]  Обновить PKMS-список
echo([9]  Установить Office/документы
echo([A]  Обновить Office/документы
echo([B]  Установить медиа-список
echo([C]  Обновить медиа-список
echo([D]  Установить браузеры/VPN
echo([E]  Обновить браузеры/VPN
echo([F]  Установить железо/бенчмарки
echo([G]  Обновить железо/бенчмарки
echo([H]  Применить пины
echo([I]  Рантаймы MSVC
echo([J]  Инструменты
echo([K]  Установить один пакет из списков
echo([L]  Обновить один пакет из списков
echo([Q]  Выход
echo.
choice /C 0123456789ABCDEFGHIJKLQ /N /M "Выбор: "
if errorlevel 23 goto END
if errorlevel 22 goto UPDATE_SINGLE
if errorlevel 21 goto INSTALL_SINGLE
if errorlevel 20 goto Инструменты
if errorlevel 19 goto MSVC
if errorlevel 18 goto PIN
if errorlevel 17 goto UPDATE_HW
if errorlevel 16 goto INSTALL_HW
if errorlevel 15 goto UPDATE_NET
if errorlevel 14 goto INSTALL_NET
if errorlevel 13 goto UPDATE_MEDIA
if errorlevel 12 goto INSTALL_MEDIA
if errorlevel 11 goto UPDATE_OFFICE
if errorlevel 10 goto INSTALL_OFFICE
if errorlevel 9  goto UPDATE_PKMS
if errorlevel 8  goto INSTALL_PKMS
if errorlevel 7  goto UPDATE_AI
if errorlevel 6  goto INSTALL_AI
if errorlevel 5  goto UPDATE_DEV
if errorlevel 4  goto INSTALL_DEV
if errorlevel 3  goto UPDATE_SYSTEM
if errorlevel 2  goto INSTALL_SYSTEM
if errorlevel 1  goto WG_UPDATE
goto MAIN

:WG_UPDATE
call "%ROOT%system_core\winget\package_apps\WinGet-Update.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_SYSTEM
call :RUN_LIST install "%ROOT%config\system.txt"
goto RETURN_MAIN

:UPDATE_SYSTEM
call :RUN_LIST update "%ROOT%config\system.txt"
goto RETURN_MAIN

:INSTALL_DEV
call :RUN_LIST install "%ROOT%config\dev.txt"
goto RETURN_MAIN

:UPDATE_DEV
call :RUN_LIST update "%ROOT%config\dev.txt"
goto RETURN_MAIN

:INSTALL_AI
call :RUN_LIST install "%ROOT%config\ai.txt"
goto RETURN_MAIN

:UPDATE_AI
call :RUN_LIST update "%ROOT%config\ai.txt"
goto RETURN_MAIN

:INSTALL_PKMS
call :RUN_LIST install "%ROOT%config\pkms.txt"
goto RETURN_MAIN

:UPDATE_PKMS
call :RUN_LIST update "%ROOT%config\pkms.txt"
goto RETURN_MAIN

:INSTALL_OFFICE
call :RUN_LIST install "%ROOT%config\office.txt"
goto RETURN_MAIN

:UPDATE_OFFICE
call :RUN_LIST update "%ROOT%config\office.txt"
goto RETURN_MAIN

:INSTALL_MEDIA
call :RUN_LIST install "%ROOT%config\media.txt"
goto RETURN_MAIN

:UPDATE_MEDIA
call :RUN_LIST update "%ROOT%config\media.txt"
goto RETURN_MAIN

:INSTALL_NET
call :RUN_LIST install "%ROOT%config\browsers-vpn.txt"
goto RETURN_MAIN

:UPDATE_NET
call :RUN_LIST update "%ROOT%config\browsers-vpn.txt"
goto RETURN_MAIN

:INSTALL_HW
call :RUN_LIST install "%ROOT%config\hardware-benchmarks.txt"
goto RETURN_MAIN

:UPDATE_HW
call :RUN_LIST update "%ROOT%config\hardware-benchmarks.txt"
goto RETURN_MAIN

:PIN
call :RUN_LIST pin "%ROOT%config\pins.txt"
goto RETURN_MAIN

:MSVC
call "%ROOT%cli\Launcher-Audion-MSVC-Legacy-RU.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:Инструменты
call "%ROOT%cli\Launcher-Audion-Tools-RU.cmd"
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

:RUN_LIST
set "WINGET_CONFIRM=1"
call "%ROOT%system_core\winget\scripts\Launch-WinGet-Lists.cmd" "%ROOT%" "%~1" "%~2"
set "RC=%ERRORLEVEL%"
set "WINGET_CONFIRM="
exit /b %RC%

:RETURN_MAIN
if "%RC%"=="200" goto MAIN
echo.
echo([INFO] Процесс завершён с кодом %RC%.
echo([INFO] Нажмите любую клавишу, чтобы вернуться в главное меню.
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
