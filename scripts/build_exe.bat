@echo off
cd /d "%~dp0.."
python tools\build_exe.py --prepare-local-build
if errorlevel 1 exit /b 1
echo.
echo Done: dist\HaloBattery\HaloBattery.exe (keep the whole HaloBattery folder together)
pause
