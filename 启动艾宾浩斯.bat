@echo off
rem Ebbinghaus launcher (alternative to the .vbs). Pure ASCII, relative path.
cd /d "%~dp0"
where pythonw.exe >nul 2>nul && ( start "" pythonw.exe app.py & exit /b )
where pyw.exe     >nul 2>nul && ( start "" pyw.exe app.py     & exit /b )
where python.exe  >nul 2>nul && ( start "" python.exe app.py  & exit /b )
echo Python not found. Install Python 3 (tick "Add Python to PATH"), then try again.
pause
