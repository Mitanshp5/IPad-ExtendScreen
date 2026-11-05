@echo off
setlocal
cd /d "%~dp0"

set "PYTHONW=C:\Users\Mitansh\AppData\Local\Programs\Python\Python312\pythonw.exe"

if not exist "%PYTHONW%" (
    for /f "delims=" %%i in ('py -3 -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set "PYTHONW=%%i"
)

if exist "%PYTHONW%" (
    start "" "%PYTHONW%" "%~dp0host-tools\tray-host.py"
) else (
    start "" pythonw "%~dp0host-tools\tray-host.py"
)

endlocal
