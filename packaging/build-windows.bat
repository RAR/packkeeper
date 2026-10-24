@echo off
rem Build PackKeeper on this Windows PC: tests, then PackKeeper.exe and
rem packkeeper-cli.exe, zipped as packkeeper-windows-x86_64.zip in the source
rem folder. Uses no CI minutes.
rem
rem Copy the source folder to this PC's disk first (not a USB stick: the build
rem writes thousands of files). Then double-click this file, or run it from a
rem Command Prompt. The first run needs internet: it installs uv (Astral's
rem Python manager) if it is missing, and uv fetches Python 3.12 and the
rem packages. Later runs reuse them.

setlocal
cd /d "%~dp0.."

where uv >nul 2>nul
if errorlevel 1 (
    echo Installing uv...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" || goto :failed
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

set QT_QPA_PLATFORM=offscreen
set "UV=uv run --python 3.12 --extra gui --extra dev"

echo Running the tests...
%UV% python -m pytest -q || goto :failed

echo Building...
%UV% python packaging\build.py || goto :failed

powershell -NoProfile -Command "Compress-Archive -Force -Path dist\PackKeeper.exe, dist\cli\packkeeper-cli.exe, README.md -DestinationPath packkeeper-windows-x86_64.zip" || goto :failed

echo.
echo Built: %CD%\packkeeper-windows-x86_64.zip
echo   (or run dist\PackKeeper.exe and dist\cli\packkeeper-cli.exe directly)
pause
exit /b 0

:failed
echo.
echo FAILED: see the messages above.
pause
exit /b 1
