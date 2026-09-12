@echo off
rem Diagnostic launcher: runs the app in THIS console so errors are visible.
rem If double-clicking the normal launcher does nothing, run this and read the messages.
cd /d "%~dp0"
echo Working folder: %CD%
echo(
where python.exe >nul 2>nul || (
    echo [X] python.exe not found on PATH.
    echo     Install Python 3 from https://www.python.org and tick "Add Python to PATH".
    echo(
    pause
    exit /b
)
echo Starting app.py ... (close the app window to return here)
echo(
python.exe app.py
echo(
echo === app.py has exited. If a "Traceback" is shown above, copy it to me. ===
pause
