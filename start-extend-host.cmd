@echo off
setlocal
cd /d "%~dp0"
echo Starting Extend Screen host in EXTEND mode...
echo The iPad should be on the same Wi-Fi network.
echo The PC stays single-screen until the iPad starts streaming.
echo Press Ctrl+C to stop the host.
echo.
py -3 host-tools\screen-host.py --extend
if errorlevel 1 pause
endlocal
