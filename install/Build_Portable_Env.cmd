@echo off
chcp 65001 >nul
setlocal EnableExtensions

title Audion Get Tools - Build Portable Env PS

call "%~dp0Build_GUI_Runtime.cmd" %*
exit /b %errorlevel%
