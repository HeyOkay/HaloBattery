@echo off
chcp 65001 >nul
cd /d "%~dp0"
python halo_battery.pyw --probe
pause
