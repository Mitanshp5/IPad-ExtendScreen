@echo off
setlocal
cd /d "%~dp0"

for /f "delims=" %%i in ('py -3 -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do (
    set "PYTHONW=%%i"
)

if exist "%PYTHONW%" (
    start "" "%PYTHONW%" "%~dp0host-tools\tray-host.py"
) else (
    start "" py -3 "%~dp0host-tools\tray-host.py"
)

endlocal
