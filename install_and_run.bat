@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (echo Failed to install dependencies & pause & exit /b 1)
start "" pythonw halo_battery.pyw
