' 开启开机自启 (double-click me once).
' Registers the schedule app to auto-start (minimized to the tray) at every login,
' and starts it right now. The app shows a tray icon and fires the scheduled reminders.
Option Explicit
Dim fso, sh, folder, startup, target, oldLnk, lnk
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
folder  = fso.GetParentFolderName(WScript.ScriptFullName)
startup = sh.SpecialFolders("Startup")
target  = folder & "\run_app_bg.vbs"

' 清掉旧版本(只跑 reminder.py)的启动项, 避免重复通知
oldLnk = startup & "\EbbinghausReminder.lnk"
If fso.FileExists(oldLnk) Then fso.DeleteFile(oldLnk)

Set lnk = sh.CreateShortcut(startup & "\EbbinghausApp.lnk")
lnk.TargetPath = target
lnk.WorkingDirectory = folder
lnk.Description = "Ebbinghaus schedule autostart (tray)"
lnk.Save

' 立即启动一次, 无需等到下次开机 (会缩进右下角托盘运行)
sh.Run "wscript.exe """ & target & """", 0, False

MsgBox "Autostart ENABLED. The app will start minimized to the tray at every login (and just started now). Look for the tray icon at the bottom-right.", 64, "Ebbinghaus"
