@echo off
cd /d "%~dp0.."
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1
python tools\make_icon.py halo.ico
if errorlevel 1 exit /b 1
python tools\make_version.py version_info.txt
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_settings.ps1
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --onedir --windowed --noupx --name HaloBattery --icon halo.ico --version-file version_info.txt --hidden-import hid --hidden-import pystray._win32 --hidden-import serial --hidden-import serial.tools.list_ports --add-data "docs/licenses:licenses" --add-data "docs/credits.md:licenses" --add-data ".build/settings-ui:settings-ui" halo_battery.pyw
if errorlevel 1 exit /b 1
echo.
echo Done: dist\HaloBattery\HaloBattery.exe (keep the whole HaloBattery folder together)
pause
