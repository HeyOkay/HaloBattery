@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
python tools\make_icon.py halo.ico
python -m PyInstaller --noconfirm --onefile --windowed --name HaloBattery --icon halo.ico --hidden-import hid --hidden-import pystray._win32 halo_battery.pyw
echo.
echo Done: dist\HaloBattery.exe
pause
