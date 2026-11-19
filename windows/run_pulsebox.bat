@echo off
rem PulseBox launcher. Uses pythonw so no console window stays open.
rem To see the log output instead, run:  python pulsebox.py
setlocal
set "PULSEBOX_DIR=%~dp0"
if "%PULSEBOX_DIR:~-1%"=="\" set "PULSEBOX_DIR=%PULSEBOX_DIR:~0,-1%"
cd /d "%PULSEBOX_DIR%"
start "" pythonw "%PULSEBOX_DIR%\pulsebox.py" %*
