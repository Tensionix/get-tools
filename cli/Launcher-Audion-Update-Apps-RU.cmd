@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

for %%A in ("%~dp0..") do set "ROOT=%%~fA\"

set "RUNTIME_DIR=%ROOT%._runtime"
set "MENU_FILE=%RUNTIME_DIR%\updateapps_menu_ru.txt"
set "RES_FILE=%RUNTIME_DIR%\updateapps_menu_ru_res.txt"

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
echo   Audion - обновление приложений
echo ================================================================
echo Режим меню: %MENU_MODE%
echo.

if defined FZF_CMD goto FZF_MENU
goto FALLBACK_MENU

:FZF_MENU
>  "%MENU_FILE%" echo [01] Обновить Docker Desktop       ^| docker      ^| Docker.DockerDesktop
>> "%MENU_FILE%" echo [02] Обновить Node.js LTS          ^| nodejs      ^| OpenJS.NodeJS.LTS
>> "%MENU_FILE%" echo [03] Обновить Obsidian             ^| obsidian    ^| Obsidian.Obsidian
>> "%MENU_FILE%" echo [04] Обновить PowerShell 7+        ^| powershell  ^| Microsoft.PowerShell
>> "%MENU_FILE%" echo [05] Обновить Python 3.12          ^| python      ^| Python.Python.3.12
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [06] Обновить Shutter Encoder 19.8 ^| shutter     ^| PaulPacifico.ShutterEncoder
>> "%MENU_FILE%" echo [07] Обновить Windows Terminal     ^| winterminal ^| Microsoft.WindowsTerminal
>> "%MENU_FILE%" echo [08] Обновить LosslessCut          ^| losslesscut ^| mifi.losslesscut
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [09] Обновить MSVC 2015+ x86/x64   ^| msvc2015    ^| прямая загрузка Microsoft
>>"%MENU_FILE%" echo.
>> "%MENU_FILE%" echo [00] 🚪 Выход                      ^| exit        ^| закрыть

"%FZF_CMD%" --bind="backspace:abort" --prompt="audion@update-apps > " --pointer=">" --header="Выберите приложение для обновления:" --layout=reverse --border=rounded --info=hidden --margin=1,2 < "%MENU_FILE%" > "%RES_FILE%"

set "CHOICE="
set /p CHOICE=<"%RES_FILE%"
if not defined CHOICE exit /b 200

for /f "tokens=2 delims=|" %%a in ("%CHOICE%") do set "RAW=%%a"
call :TRIM RAW

if /I "%RAW%"=="docker"      goto UPDATE_DOCKER
if /I "%RAW%"=="nodejs"      goto UPDATE_NODEJS
if /I "%RAW%"=="obsidian"    goto UPDATE_OBSIDIAN
if /I "%RAW%"=="powershell"  goto UPDATE_POWERSHELL
if /I "%RAW%"=="python"      goto UPDATE_PYTHON
if /I "%RAW%"=="shutter"     goto UPDATE_SHUTTER
if /I "%RAW%"=="winterminal" goto UPDATE_WINTERMINAL
if /I "%RAW%"=="losslesscut" goto UPDATE_LOSSLESSCUT
if /I "%RAW%"=="msvc2015"    goto UPDATE_MSVC2015
if /I "%RAW%"=="exit"        goto END
goto MAIN

:FALLBACK_MENU
echo([1] Обновить Docker Desktop
echo([2] Обновить Node.js LTS
echo([3] Обновить Obsidian
echo([4] Обновить PowerShell 7+
echo([5] Обновить Python 3.12
echo([6] Обновить Shutter Encoder 19.8
echo([7] Обновить Windows Terminal
echo([8] Обновить LosslessCut
echo([9] Обновить MSVC 2015+ x86/x64 - прямой установщик Microsoft
echo([0] Выход
echo.
choice /C 1234567890 /N /M "Выбор: "
if errorlevel 10 goto END
if errorlevel 9  goto UPDATE_MSVC2015
if errorlevel 8  goto UPDATE_LOSSLESSCUT
if errorlevel 7  goto UPDATE_WINTERMINAL
if errorlevel 6  goto UPDATE_SHUTTER
if errorlevel 5  goto UPDATE_PYTHON
if errorlevel 4  goto UPDATE_POWERSHELL
if errorlevel 3  goto UPDATE_OBSIDIAN
if errorlevel 2  goto UPDATE_NODEJS
if errorlevel 1  goto UPDATE_DOCKER
goto MAIN

:UPDATE_DOCKER
call :CONFIRM_RUN "Обновить Docker Desktop"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Update-DockerDesktop.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_NODEJS
call :CONFIRM_RUN "Обновить Node.js LTS"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Update-NodeJS-LTS.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_OBSIDIAN
call :CONFIRM_RUN "Обновить Obsidian"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Update-Obsidian.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_POWERSHELL
call :CONFIRM_RUN "Обновить PowerShell 7+"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Update-PowerShell.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_PYTHON
call :CONFIRM_RUN "Обновить Python 3.12"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Update-Python-3.12.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_SHUTTER
call :CONFIRM_RUN "Обновить Shutter Encoder 19.8"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Update-ShutterEncoder-19.8.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_WINTERMINAL
call :CONFIRM_RUN "Обновить Windows Terminal"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Update-WindowsTerminal.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_LOSSLESSCUT
call :CONFIRM_RUN "Обновить LosslessCut"
if errorlevel 2 goto END
if errorlevel 1 goto MAIN
call "%ROOT%system_core\winget\install_apps\Update-LosslessCut.cmd"
set "RC=%ERRORLEVEL%"
goto RETURN_MAIN

:UPDATE_MSVC2015
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
echo([INFO] Нажмите любую клавишу, чтобы вернуться в меню обновления.
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
