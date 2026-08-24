@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

for %%A in ("%~dp0..") do set "ROOT=%%~fA\"

set "RUNTIME_DIR=%ROOT%._runtime"
set "MENU_FILE=%RUNTIME_DIR%\installapps_menu_ru.txt"
set "RES_FILE=%RUNTIME_DIR%\installapps_menu_ru_res.txt"

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
echo   Audion - установка приложений
echo ================================================================
echo Режим меню: %MENU_MODE%
echo.

if defined FZF_CMD goto FZF_MENU
goto FALLBACK_MENU

:FZF_MENU
>  "%MENU_FILE%" echo [01] Установить MSVC 2015+ (x86/x64)       ^| msvc2015    ^| VCRedist.2015+.x86 + x64
>> "%MENU_FILE%" echo [02] Установить MSVC Legacy (2005-2013)    ^| msvc_legacy ^| 2005 2008 2010 2013
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [03] Установить Docker Desktop             ^| docker      ^| Docker.DockerDesktop  [интерактивно]
>> "%MENU_FILE%" echo [04] Установить Node.js LTS                ^| nodejs      ^| OpenJS.NodeJS.LTS
>> "%MENU_FILE%" echo [05] Установить Obsidian                   ^| obsidian    ^| Obsidian.Obsidian
>> "%MENU_FILE%" echo [06] Установить PowerShell 7+              ^| powershell  ^| Microsoft.PowerShell
>> "%MENU_FILE%" echo [07] Установить Python 3.12                ^| python      ^| Python.Python.3.12
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [08] Установить Shutter Encoder 19.8       ^| shutter     ^| PaulPacifico.ShutterEncoder  закрепленная версия
>> "%MENU_FILE%" echo [09] Установить Shutter Encoder 19.8 + PIN ^| shutter_pin ^| установка и PIN на 19.8
>> "%MENU_FILE%" echo [10] Установить Windows Terminal           ^| winterminal ^| Microsoft.WindowsTerminal
>> "%MENU_FILE%" echo [11] Установить LosslessCut                ^| losslesscut ^| mifi.losslesscut
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [00] 🚪 Выход                              ^| exit        ^| закрыть

"%FZF_CMD%" --bind="backspace:abort" --prompt="audion@install-apps > " --pointer=">" --header="Выберите приложение для установки:" --layout=reverse --border=rounded --info=hidden --margin=1,2 < "%MENU_FILE%" > "%RES_FILE%"

set "CHOICE="
set /p CHOICE=<"%RES_FILE%"
if not defined CHOICE exit /b 200

for /f "tokens=2 delims=|" %%a in ("%CHOICE%") do set "RAW=%%a"
call :TRIM RAW

if /I "%RAW%"=="msvc2015"    goto INSTALL_MSVC2015
if /I "%RAW%"=="msvc_legacy" goto INSTALL_MSVC_LEGACY
if /I "%RAW%"=="docker"      goto INSTALL_DOCKER
if /I "%RAW%"=="nodejs"      goto INSTALL_NODEJS
if /I "%RAW%"=="obsidian"    goto INSTALL_OBSIDIAN
if /I "%RAW%"=="powershell"  goto INSTALL_POWERSHELL
if /I "%RAW%"=="python"      goto INSTALL_PYTHON
if /I "%RAW%"=="shutter"     goto INSTALL_SHUTTER
if /I "%RAW%"=="shutter_pin" goto INSTALL_SHUTTER_PIN
if /I "%RAW%"=="winterminal" goto INSTALL_WINTERMINAL
if /I "%RAW%"=="losslesscut" goto INSTALL_LOSSLESSCUT
if /I "%RAW%"=="exit"        goto END
goto MAIN

:FALLBACK_MENU
echo([1]  Установить MSVC 2015+ (x86/x64)
echo([2]  Установить MSVC Legacy (2005-2013)
echo([3]  Установить Docker Desktop
echo([4]  Установить Node.js LTS
echo([5]  Установить Obsidian
echo([6]  Установить PowerShell 7+
echo([7]  Установить Python 3.12
echo([8]  Установить Shutter Encoder 19.8
echo([9]  Установить Shutter Encoder 19.8 + пин
echo([A]  Установить Windows Terminal
echo([B]  Установить LosslessCut
echo([0]  Выход
echo.
choice /C 123456789AB0 /N /M "Выбор: "
if errorlevel 12 goto END
if errorlevel 11 goto INSTALL_LOSSLESSCUT
if errorlevel 10 goto INSTALL_WINTERMINAL
if errorlevel 9  goto INSTALL_SHUTTER_PIN
if errorlevel 8  goto INSTALL_SHUTTER
if errorlevel 7  goto INSTALL_PYTHON
if errorlevel 6  goto INSTALL_POWERSHELL
if errorlevel 5  goto INSTALL_OBSIDIAN
if errorlevel 4  goto INSTALL_NODEJS
if errorlevel 3  goto INSTALL_DOCKER
if errorlevel 2  goto INSTALL_MSVC_LEGACY
if errorlevel 1  goto INSTALL_MSVC2015
goto MAIN

:INSTALL_MSVC2015
call :CONFIRM_RUN "Установить MSVC 2015+ x86/x64"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-Audion-MSVC-2015+.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_MSVC_LEGACY
call :CONFIRM_RUN "Установить MSVC Legacy 2005-2013"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-Audion-MSVC.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_DOCKER
call :CONFIRM_RUN "Установить Docker Desktop"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-DockerDesktop.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_NODEJS
call :CONFIRM_RUN "Установить Node.js LTS"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-NodeJS-LTS.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_OBSIDIAN
call :CONFIRM_RUN "Установить Obsidian"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-Obsidian.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_POWERSHELL
call :CONFIRM_RUN "Установить PowerShell 7+"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-PowerShell.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_PYTHON
call :CONFIRM_RUN "Установить Python 3.12"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-Python-3.12.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_SHUTTER
call :CONFIRM_RUN "Установить Shutter Encoder 19.8"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-ShutterEncoder-19.8.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_SHUTTER_PIN
call :CONFIRM_RUN "Установить Shutter Encoder 19.8 and pin it"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-ShutterEncoder-19.8.cmd"
call "%ROOT%system_core\winget\install_apps\Pin-ShutterEncoder-19.8.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_WINTERMINAL
call :CONFIRM_RUN "Установить Windows Terminal"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-WindowsTerminal.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:INSTALL_LOSSLESSCUT
call :CONFIRM_RUN "Установить LosslessCut"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Install-LosslessCut.cmd"
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
echo([INFO] Нажмите любую клавишу, чтобы вернуться в меню установки.
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
