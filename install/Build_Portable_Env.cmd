@echo off
chcp 65001 >nul
setlocal EnableExtensions

title Audion Get Tools - Build Portable Env PS

set "PS_EXE="
set "PS1_FILE=%~dpn0.ps1"

if exist "%~dp0..\App\AudionPs.exe" set "PS_EXE=%~dp0..\App\AudionPs.exe"
if not defined PS_EXE where pwsh.exe >nul 2>nul && set "PS_EXE=pwsh.exe"
if not defined PS_EXE where powershell.exe >nul 2>nul && set "PS_EXE=powershell.exe"

if not defined PS_EXE (
    echo [ERROR] PowerShell was not found.
    echo [INFO] The program carries its own in App\AudionPs.exe: install\Build-App.cmd builds it.
    if not defined AUDION_NO_PAUSE pause
    exit /b 1
)

"%PS_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1_FILE%" %*
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (
    echo [OK] Build_Portable_Env.ps1 finished successfully.
) else (
    echo [ERROR] Build_Portable_Env.ps1 finished with exit code %RC%.
)

if not defined AUDION_NO_PAUSE pause
exit /b %RC%
