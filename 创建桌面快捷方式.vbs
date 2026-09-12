' Creates a Desktop shortcut for the app (double-click me once).
Option Explicit
Dim fso, sh, folder, desktop, appName, launcher, lnk
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
folder  = fso.GetParentFolderName(WScript.ScriptFullName)
desktop = sh.SpecialFolders("Desktop")

' appName = 艾宾浩斯日程表 ; launcher file = 启动艾宾浩斯.vbs (built via ChrW to avoid encoding issues)
appName  = ChrW(&H827E) & ChrW(&H5BBE) & ChrW(&H6D69) & ChrW(&H65AF) & ChrW(&H65E5) & ChrW(&H7A0B) & ChrW(&H8868)
launcher = ChrW(&H542F) & ChrW(&H52A8) & ChrW(&H827E) & ChrW(&H5BBE) & ChrW(&H6D69) & ChrW(&H65AF) & ".vbs"

Set lnk = sh.CreateShortcut(desktop & "\" & appName & ".lnk")
lnk.TargetPath = folder & "\" & launcher
lnk.WorkingDirectory = folder
lnk.Description = appName
lnk.Save

MsgBox "Desktop shortcut created: " & appName, 64, "Ebbinghaus"
