@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo.
echo WinGet import helper
echo.
set "IN="
set /p IN=Enter import path ^(.json^): 
if not defined IN goto :noinput
if not exist "%IN%" goto :missing

set "FLAGS=--accept-package-agreements --accept-source-agreements"

set "ANS="
set /p ANS=Ignore versions from JSON and install latest instead? [Y/N]: 
if /I "%ANS%"=="Y" set "FLAGS=%FLAGS% --ignore-versions"

set "ANS="
set /p ANS=Skip upgrades when the package is already installed? [Y/N]: 
if /I "%ANS%"=="Y" set "FLAGS=%FLAGS% --no-upgrade"

set "ANS="
set /p ANS=Ignore unavailable packages and continue? [Y/N]: 
if /I "%ANS%"=="Y" set "FLAGS=%FLAGS% --ignore-unavailable"

echo.
echo [INFO] Importing from:
echo        %IN%
echo.
winget import -i "%IN%" %FLAGS%

echo.
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal
goto :eof

:noinput
echo.
echo [ERROR] No input file provided.
echo.
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal
goto :eof

:missing
echo.
echo [ERROR] File not found:
echo         %IN%
echo.
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal
