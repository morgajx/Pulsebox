@echo off
rem =============================================================================
rem PulseBox - Nuitka build for Windows (counterpart of the Pi's build.sh)
rem =============================================================================
rem Compiles pulsebox.py + core\ and ui\ into dist\pulsebox.exe. modules\ is
rem copied alongside as plain Python so dynamic plugin loading keeps working.
rem
rem NOTE: written to mirror build.sh but NOT yet run on a Windows machine.
rem Nuitka needs a C compiler; it offers to download one on first use.
rem
rem   pip install nuitka ordered-set zstandard
rem =============================================================================
setlocal
cd /d "%~dp0"

python -m nuitka --version >nul 2>nul
if errorlevel 1 (
    echo Nuitka not found. Install it with:
    echo   pip install nuitka ordered-set zstandard
    exit /b 1
)

rem Plugins are loaded from source at run time, so anything only THEY import is
rem invisible to Nuitka and has to be named here.
set EXTRA=--include-module=winreg --include-module=http.server --include-package=mutagen
python -c "import spotipy" >nul 2>nul && set EXTRA=%EXTRA% --include-package=spotipy

python -m nuitka ^
    --standalone --onefile ^
    --windows-console-mode=disable ^
    --output-filename=pulsebox.exe ^
    --output-dir=dist ^
    --include-package=core ^
    --include-package=ui ^
    %EXTRA% ^
    --include-data-files=assets\splash.png=assets\splash.png ^
    --include-data-files=assets\splash_logo.png=assets\splash_logo.png ^
    --enable-plugin=tk-inter ^
    pulsebox.py
if errorlevel 1 exit /b 1

if exist dist\modules rmdir /s /q dist\modules
xcopy /e /i /q modules dist\modules >nul
for /d /r dist\modules %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"
xcopy /e /i /q /y assets dist\assets >nul

echo.
echo Build complete: dist\pulsebox.exe
echo Keep dist\modules and dist\assets next to the exe.
