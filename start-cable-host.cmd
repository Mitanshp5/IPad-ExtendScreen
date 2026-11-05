@echo off
setlocal
cd /d "%~dp0"
echo Starting Extend Screen host in USB CABLE + EXTEND mode at 45 fps...
echo.
echo On the iPad:
echo   1. Open Extend Screen -^> Settings.
echo   2. Set Connection mode to USB cable. No password or code is needed.
echo   3. Tap Save, then connect the iPad by USB and tap Trust if asked.
echo.
echo The PC connects through Apple usbmuxd and streams the extended desktop.
echo The PC stays single-screen until the iPad connects, then returns to single-screen.
echo Press Ctrl+C to stop.
echo.
py -3 host-tools\screen-host.py --cable --extend --fps 45
if errorlevel 1 pause
endlocal
