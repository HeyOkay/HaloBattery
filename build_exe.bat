@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
python tools\make_icon.py halo.ico
python tools\make_version.py version_info.txt
python -m PyInstaller --noconfirm --onedir --windowed --noupx --name HaloBattery --icon halo.ico --version-file version_info.txt --hidden-import hid --hidden-import pystray._win32 halo_battery.pyw
echo.
echo Done: dist\HaloBattery\HaloBattery.exe (keep the whole HaloBattery folder together)
pause
