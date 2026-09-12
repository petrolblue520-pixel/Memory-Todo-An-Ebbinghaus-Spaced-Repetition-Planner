' 艾宾浩斯日程表 - background launcher (used by the startup shortcut)
' Starts app.py minimized to the system tray with a windowless Python (no console flash).
' The app itself shows the tray icon and fires the scheduled reminders.
Option Explicit
Dim fso, sh, folder
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = folder

On Error Resume Next
sh.Run "pythonw.exe app.py --minimized", 0, False
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "pyw.exe app.py --minimized", 0, False
End If
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "python.exe app.py --minimized", 0, False
End If
