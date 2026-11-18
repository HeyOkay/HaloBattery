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
python tools/build_exe.py
if errorlevel 1 exit /b 1
echo.
echo Done: dist\HaloBattery\HaloBattery.exe (keep the whole HaloBattery folder together)
pause
