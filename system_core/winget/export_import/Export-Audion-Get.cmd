@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo.
echo WinGet export helper
echo.
set "OUT="
set /p OUT=Enter export path ^(.json^) [default: %USERPROFILE%\Desktop\winget-export.json]: 
if not defined OUT set "OUT=%USERPROFILE%\Desktop\winget-export.json"

for %%I in ("%OUT%") do (
    set "OUT=%%~fI"
    set "DIR=%%~dpI"
)

if /I not "%OUT:~-5%"==".json" set "OUT=%OUT%.json"
if not exist "%DIR%" mkdir "%DIR%" >nul 2>nul

echo.
echo [INFO] Exporting installed winget-matched apps to:
echo        %OUT%
echo.
winget export -o "%OUT%" --source winget --include-versions --accept-source-agreements

echo.
echo.
echo [INFO] Press any key to close this window.
if not defined AUDION_NO_PAUSE pause
endlocal
