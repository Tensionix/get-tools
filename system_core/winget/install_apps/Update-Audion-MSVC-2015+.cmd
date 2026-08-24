@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "EXIT_RC=0"

rem Messages and comments are intentionally kept in English.

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..\..") do set "ROOT=%%~fI"
set "DL=%ROOT%\install\download"

set "VC_X86_URL=https://aka.ms/vc14/vc_redist.x86.exe"
set "VC_X64_URL=https://aka.ms/vc14/vc_redist.x64.exe"

set "VC_X86_EXE=%DL%\vc_redist.x86.exe"
set "VC_X64_EXE=%DL%\vc_redist.x64.exe"

echo ================================================================
echo AUDION MSVC 2015+ DIRECT UPDATE
echo ================================================================
echo [INFO] This script downloads and runs the official Microsoft MSVC 2015+ (v14) installers.
echo [INFO] Download dir: %DL%
echo.

call :download "%VC_X86_URL%" "%VC_X86_EXE%" "MSVC 2015+ x86"
set "STEP_RC=%ERRORLEVEL%"
if not "%STEP_RC%"=="0" (
    set "EXIT_RC=%STEP_RC%"
    goto :end
)

call :download "%VC_X64_URL%" "%VC_X64_EXE%" "MSVC 2015+ x64"
set "STEP_RC=%ERRORLEVEL%"
if not "%STEP_RC%"=="0" (
    set "EXIT_RC=%STEP_RC%"
    goto :end
)

call :install "%VC_X86_EXE%" "MSVC 2015+ x86"
call :REMEMBER_STEP
call :install "%VC_X64_EXE%" "MSVC 2015+ x64"
call :REMEMBER_STEP

echo.
echo [INFO] Done. Installers kept in: %DL%
goto :end

:download
echo.
echo ----------------------------------------------------------------
echo [INFO] Downloading %~3
echo [INFO] URL:  %~1
echo [INFO] File: %~2

if exist "%~2" del /f /q "%~2" >nul 2>nul

curl.exe -L --fail --output "%~2" "%~1"
if "%ERRORLEVEL%"=="0" goto :download_ok

echo [WARN] curl failed, trying PowerShell...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%~1' -OutFile '%~2'"
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] Download failed for %~3
    exit /b 1
)

:download_ok
if not exist "%~2" (
    echo [ERROR] File not found after download: %~2
    exit /b 1
)
echo [OK] Downloaded %~3
exit /b 0

:install
echo.
echo ----------------------------------------------------------------
echo [INFO] Installing %~2
echo [INFO] EXE: %~1

start "" /wait "%~1" /install /passive /norestart
set "RC=%ERRORLEVEL%"

if "%RC%"=="0"    ( echo [OK] Installed or updated %~2 & exit /b 0 )
if "%RC%"=="1638" ( echo [INFO] Newer version already installed: %~2 & exit /b 0 )
if "%RC%"=="3010" ( echo [OK] Installed %~2 - restart required & exit /b 0 )
if "%RC%"=="1641" ( echo [OK] Installed %~2 - restart initiated & exit /b 0 )

echo [WARN] Installer returned errorlevel %RC% for %~2
exit /b %RC%

:REMEMBER_STEP
set "STEP_RC=%ERRORLEVEL%"
if not "%STEP_RC%"=="0" if "%EXIT_RC%"=="0" set "EXIT_RC=%STEP_RC%"
exit /b 0

:end
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal & exit /b %EXIT_RC%
