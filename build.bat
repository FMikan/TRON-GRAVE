@echo off
REM ============================================================================
REM  TRON-GRAVE  -  build a single-file Windows executable.
REM
REM  What it does: creates an isolated build virtualenv, installs the runtime
REM  dependencies + PyInstaller, and packs the whole app into ONE .exe using
REM  TRON-GRAVE.spec. Output: dist\TRON-GRAVE.exe
REM
REM  Requirements: Windows + Python 3.11 or newer installed
REM  (https://www.python.org/downloads/ -- tick "Add python.exe to PATH").
REM  Then just double-click this file, or run it from a terminal.
REM ============================================================================
setlocal
cd /d "%~dp0"

echo(
echo === TRON-GRAVE Windows build ===
echo(

REM --- locate a Python interpreter (prefer the "py" launcher) ------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo ERROR: Python was not found on PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" during setup^), then run this again.
    goto :fail
)
echo Using Python: %PY%
%PY% --version || goto :fail

REM --- create / reuse an isolated build virtualenv ----------------------------
set "VENV=.build-venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating build virtualenv in "%VENV%" ...
    %PY% -m venv "%VENV%" || goto :fail
)
set "VPY=%VENV%\Scripts\python.exe"

echo Installing dependencies and PyInstaller ...
"%VPY%" -m pip install --upgrade pip                    || goto :fail
"%VPY%" -m pip install -r requirements.txt pyinstaller  || goto :fail

REM --- build the one-file exe -------------------------------------------------
echo(
echo Building one-file exe (this can take a minute) ...
"%VPY%" -m PyInstaller --clean --noconfirm TRON-GRAVE.spec || goto :fail

echo(
if not exist "dist\TRON-GRAVE.exe" (
    echo ERROR: build finished but dist\TRON-GRAVE.exe was not produced.
    goto :fail
)
echo === DONE ===
echo Output: "%CD%\dist\TRON-GRAVE.exe"
echo Attach that single file to the GitHub release.
echo(
pause
exit /b 0

:fail
echo(
echo *** BUILD FAILED -- see the messages above. ***
echo(
pause
exit /b 1
