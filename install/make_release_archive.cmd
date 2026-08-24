@echo off
setlocal EnableExtensions

title Audion Get Tools - Make Release Archive

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
for %%A in ("%SCRIPT_DIR%\..") do set "ROOT=%%~fA"
for %%A in ("%ROOT%") do set "PROJECT_NAME=%%~nxA"

set "RELEASE_DIR=%ROOT%\release"
set "STAGE_PARENT=%RELEASE_DIR%\_stage"
set "STAGE_PROJECT=%STAGE_PARENT%\%PROJECT_NAME%"
set "ARCHIVE=%RELEASE_DIR%\%PROJECT_NAME%_portable.zip"

if not exist "%RELEASE_DIR%\" mkdir "%RELEASE_DIR%" >nul 2>nul
if exist "%STAGE_PROJECT%\" rd /s /q "%STAGE_PROJECT%" >nul 2>nul
if not exist "%STAGE_PARENT%\" mkdir "%STAGE_PARENT%" >nul 2>nul

call :BANNER

echo [1/4] Staging release contents...
robocopy "%ROOT%" "%STAGE_PROJECT%" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP ^
  /XD "%ROOT%\.git" "%ROOT%\release" "%ROOT%\logs" "%ROOT%\._runtime" ^
  /XF "%ROOT%\.gitignore"
set "RC=%errorlevel%"
if %RC% GEQ 8 goto ERR_STAGE

echo [2/4] Verifying staged structure...
if not exist "%STAGE_PROJECT%\Launcher-Audion-Get.cmd" goto ERR_STAGE
if not exist "%STAGE_PROJECT%\config\"                     goto ERR_STAGE
if not exist "%STAGE_PROJECT%\system_core\winget\scripts\" goto ERR_STAGE
if not exist "%STAGE_PROJECT%\system_core\fzf.exe"         goto ERR_STAGE
if not exist "%STAGE_PROJECT%\system_core\powershell\"     goto ERR_STAGE

echo [3/4] Building ZIP archive...
where tar.exe >nul 2>nul
if errorlevel 1 goto ERR_ARCHIVE
if exist "%ARCHIVE%" del /f /q "%ARCHIVE%" >nul 2>nul
pushd "%STAGE_PARENT%" >nul
tar.exe -a -cf "%ARCHIVE%" "%PROJECT_NAME%"
set "RC=%errorlevel%"
popd >nul
if not "%RC%"=="0" goto ERR_ARCHIVE

echo [4/4] Cleaning up staging directory...
rd /s /q "%STAGE_PROJECT%" >nul 2>nul

call :CHECKLIST_FINISH
if not defined AUDION_NO_PAUSE pause
exit /b 0

:BANNER
echo ======================================================================
echo   AUDION GET TOOLS - MAKE RELEASE ARCHIVE
echo ======================================================================
echo Root:    %ROOT%
echo Stage:   %STAGE_PROJECT%
echo Target:  %ARCHIVE%
echo.
goto :eof

:CHECKLIST_FINISH
echo.
echo Release checklist:
echo   [OK] Release staged and verified
echo   [OK] ZIP archive created: %ARCHIVE%
echo.
echo Manual review before upload:
echo   - Open the ZIP and inspect top-level contents
echo   - Verify no secrets or private configs are present
echo   - Verify Launcher-Audion-Get.cmd launches correctly
echo   - Verify system_core\fzf.exe and system_core\powershell\ are present
echo   - Upload the ZIP as a GitHub Release asset
echo.
goto :eof

:ERR_STAGE
echo.
echo [ERROR] Failed to stage release contents.
echo Check project paths, exclusions, and directory structure.
if not defined AUDION_NO_PAUSE pause
exit /b 1

:ERR_ARCHIVE
echo.
echo [ERROR] Failed to create ZIP archive.
echo Verify tar.exe is available (Windows 10 1803+) and release folder is writable.
if not defined AUDION_NO_PAUSE pause
exit /b 1
