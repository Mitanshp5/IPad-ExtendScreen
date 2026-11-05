Set WshShell = CreateObject("WScript.Shell")
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath
WshShell.Run """C:\Users\Mitansh\AppData\Local\Programs\Python\Python312\pythonw.exe"" """ & strPath & "\host-tools\tray-host.py""", 0, False
