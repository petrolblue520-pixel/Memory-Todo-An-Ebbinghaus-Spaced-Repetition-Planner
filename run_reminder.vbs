' 艾宾浩斯日程表 - reminder agent launcher
' Runs reminder.py in the background with a windowless Python (no console flash).
' This is what the startup shortcut points to.
Option Explicit
Dim fso, sh, folder
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = folder

On Error Resume Next
sh.Run "pythonw.exe reminder.py", 0, False
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "pyw.exe reminder.py", 0, False
End If
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "python.exe reminder.py", 0, False
End If
