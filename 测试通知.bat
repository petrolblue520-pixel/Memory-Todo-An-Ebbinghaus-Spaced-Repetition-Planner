@echo off
rem Test the Windows notification: sends ONE toast right now and shows the text.
rem If double-clicking does nothing or the toast never appears, read the messages here.
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
echo Sending one test notification ... (watch the bottom-right corner)
echo(
python.exe reminder.py --once
echo(
echo === Done. A toast should have appeared. If a "Traceback" is shown above, copy it to me. ===
pause
