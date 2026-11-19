@echo off
rem PulseBox for Windows - one-time setup. Installs the Python packages and
rem checks that VLC is present. Run from the pulsebox folder.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH.
    echo Install 64-bit Python 3.10 or newer from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer.
    pause
    exit /b 1
)

echo Installing Python packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo pip failed - see the messages above.
    pause
    exit /b 1
)

if exist "%ProgramFiles%\VideoLAN\VLC\libvlc.dll" (
    echo VLC found.
) else (
    echo.
    echo VLC media player was not found in "%ProgramFiles%\VideoLAN\VLC".
    echo PulseBox needs the 64-bit VLC from https://www.videolan.org/vlc/
    echo ^(it must match your Python: 64-bit Python needs 64-bit VLC^).
)

echo.
echo Done. Start PulseBox with run_pulsebox.bat
pause
