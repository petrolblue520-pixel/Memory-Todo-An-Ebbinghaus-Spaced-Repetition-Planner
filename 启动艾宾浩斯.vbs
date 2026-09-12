' 艾宾浩斯日程表 - launcher (double-click me)
' Sets the working directory to this file's folder, then starts the app
' with a windowless Python (no console flash). Falls back across interpreters.
Option Explicit
Dim fso, sh, folder
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = folder

On Error Resume Next
sh.Run "pythonw.exe app.py", 0, False
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "pyw.exe app.py", 0, False
End If
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "python.exe app.py", 0, False
End If
If Err.Number <> 0 Then
    On Error GoTo 0
    MsgBox "No Python found. Install Python 3 and tick 'Add Python to PATH', or run the diagnostic file.", 16, "Ebbinghaus"
End If
