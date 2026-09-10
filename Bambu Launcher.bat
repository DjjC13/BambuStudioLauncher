@echo off
rem Launches the Bambu Studio Launcher GUI with no console window.
rem pythonw.exe is python.exe without the black console box.
setlocal
cd /d "%~dp0"

for %%P in (pythonw.exe) do set "PYW=%%~$PATH:P"
if not defined PYW set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"

if not exist "%PYW%" (
    echo Could not find pythonw.exe.
    echo Install Python 3.10+ or edit this file to point PYW at your pythonw.exe.
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0bambu_launcher.py"
