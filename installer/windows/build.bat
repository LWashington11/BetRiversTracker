@echo off
:: BetRivers Poker Tracker - Unofficial — Developer build script
:: Compiles setup.iss with Inno Setup's command-line compiler (ISCC.exe).
::
:: Prerequisites:
::   Inno Setup 6.x must be installed — https://jrsoftware.org/isinfo.php
::   ISCC.exe is typically at: C:\Program Files (x86)\Inno Setup 6\ISCC.exe
::
:: Usage (from the installer\windows\ directory):
::   build.bat
::
:: Output: installer\windows\dist\BetRiversTracker-Setup-<version>.exe

setlocal

:: Default ISCC path — override by setting the ISCC environment variable.
if "%ISCC%"=="" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if not exist "%ISCC%" (
    echo ERROR: ISCC.exe not found at "%ISCC%"
    echo Install Inno Setup 6 from https://jrsoftware.org/isinfo.php
    echo Or set the ISCC environment variable to the correct path.
    exit /b 1
)

echo Building BetRivers Poker Tracker - Unofficial installer...
"%ISCC%" setup.iss
if %errorlevel% neq 0 (
    echo Build FAILED with error code %errorlevel%.
    exit /b %errorlevel%
)

echo.
echo Build succeeded. Output is in installer\windows\dist\
endlocal
