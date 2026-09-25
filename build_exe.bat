@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name HaloBattery --hidden-import hid --hidden-import pystray._win32 halo_battery.pyw
echo.
echo Done: dist\HaloBattery.exe
pause
