@echo off
chcp 65001 >nul
setlocal EnableExtensions

title Audion Get Tools - Verify

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
for %%A in ("%SCRIPT_DIR%\..") do set "ROOT=%%~fA"

rem What a release is checked for. The environment: Python and the packages of
rem the engine. The console command: get_cli.cmd reads the manifest - the window
rem draws by what it prints. The window: App\ is not built by a build of the
rem environment and travels as it is, so it is checked to be in place and to
rem check itself - every place of an operation finds its operation and its fields
rem in the manifest of this version.
rem
rem The window is not asked to carry the number of the project: the engine and
rem the manifest move on from version to version and the window is rebuilt only
rem when Source\ changes; the number it shows it reads from config\version.json.
rem Nothing here needs the .NET SDK.

if not exist "%ROOT%\runtime\python.exe" goto ERR_PYTHON
if not exist "%ROOT%\system_core\doctor.py" goto ERR_DOCTOR

call "%ROOT%\install\Check-CmdEncoding.cmd"
if errorlevel 1 goto ERR_CMD_ENCODING

"%ROOT%\runtime\python.exe" "%ROOT%\system_core\doctor.py"
set "RC=%errorlevel%"
if not "%RC%"=="0" goto DONE_VERIFY

echo.
echo [COMMAND] Reading the manifest through get_cli.cmd ...
call "%ROOT%\get_cli.cmd" describe >nul
set "RC=%errorlevel%"
if not "%RC%"=="0" goto ERR_COMMAND
echo [OK] get_cli.cmd reads the manifest.

echo.
echo [WINDOW] Checking the built window in App\ ...
set "RC=1"
if not exist "%ROOT%\Start.exe" goto ERR_START
if not exist "%ROOT%\App\AudionGetTools.exe" goto ERR_APP
if not exist "%ROOT%\App\AudionPs.exe" goto ERR_APP
"%ROOT%\App\AudionPs.exe" -NoLogo -NoProfile -NonInteractive -Command "exit 0" >nul 2>nul
if errorlevel 1 goto ERR_PS
echo [OK] The PowerShell of the program starts: App\AudionPs.exe.

set "CHECK_FILE=%TEMP%\get_tools_self_test_%RANDOM%%RANDOM%.txt"
if exist "%CHECK_FILE%" del /f /q "%CHECK_FILE%" >nul 2>nul
rem "start /wait": a windowed program is not waited for otherwise, and its exit code is lost.
start "" /wait "%ROOT%\App\AudionGetTools.exe" --self-test --to "%CHECK_FILE%"
set "CHECK_RC=%errorlevel%"
if not exist "%CHECK_FILE%" goto ERR_SELF_TEST
if not "%CHECK_RC%"=="0" goto ERR_SELF_TEST
rem find.exe of Windows by its path: with Git ahead in PATH "find" is GNU find, and it walks the whole drive.
for /f %%N in ('%SystemRoot%\System32\findstr.exe /B /C:"ok " "%CHECK_FILE%" ^| %SystemRoot%\System32\find.exe /C /V ""') do echo [OK] The window draws the manifest: %%N checks of it passed.
del /f /q "%CHECK_FILE%" >nul 2>nul
set "RC=0"

:DONE_VERIFY
if not defined AUDION_NO_PAUSE pause
exit /b %RC%

:ERR_PYTHON
echo [ERROR] runtime\python.exe was not found.
if not defined AUDION_NO_PAUSE pause
exit /b 1

:ERR_DOCTOR
echo [ERROR] doctor.py was not found.
if not defined AUDION_NO_PAUSE pause
exit /b 1

:ERR_CMD_ENCODING
echo [ERROR] CMD encoding check failed.
if not defined AUDION_NO_PAUSE pause
exit /b 1

:ERR_COMMAND
echo [ERROR] get_cli.cmd does not read the manifest, exit code %RC%.
goto DONE_VERIFY

:ERR_START
echo [ERROR] Start.exe is missing: install\Build-StartLauncher.cmd makes it.
goto DONE_VERIFY

:ERR_APP
echo [ERROR] The built window is missing or incomplete: App\AudionGetTools.exe and App\AudionPs.exe.
echo [INFO] install\Build-App.cmd builds it from Source\ and needs the .NET 10 SDK.
goto DONE_VERIFY

:ERR_PS
echo [ERROR] The PowerShell of the program, App\AudionPs.exe, does not start.
goto DONE_VERIFY

:ERR_SELF_TEST
echo [ERROR] The window does not pass its own check, exit code %CHECK_RC%.
if exist "%CHECK_FILE%" findstr /B /C:"FAIL" "%CHECK_FILE%"
echo [INFO] A manifest with something new for the window needs the window rebuilt: install\Build-App.cmd.
goto DONE_VERIFY
