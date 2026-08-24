@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "AUDION_APP_NAME=Audion Get"
set "AUDION_APP_ID=Audion.Tools.Audion.Get"
set "AUDION_GUI_ELEVATE=1"
set "AUDION_APP_ICON=E:\TOOLS\Refactor\Audion Get\system_core\icons\app.ico"
call "E:\TOOLS\Refactor\Audion Get\launcher_gui.cmd"
exit /b %ERRORLEVEL%
