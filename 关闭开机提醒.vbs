' 关闭开机自启 (double-click me once).
' Removes the Startup shortcut(s) and stops the app/reminder running in the background.
Option Explicit
Dim fso, sh, startup, lnkNew, lnkOld, wmi, procs, p, cmd, killed
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
startup = sh.SpecialFolders("Startup")
lnkNew  = startup & "\EbbinghausApp.lnk"
lnkOld  = startup & "\EbbinghausReminder.lnk"

If fso.FileExists(lnkNew) Then fso.DeleteFile(lnkNew)
If fso.FileExists(lnkOld) Then fso.DeleteFile(lnkOld)

' 停掉后台运行的本程序 (匹配命令行里的 app.py / reminder.py)
killed = 0
On Error Resume Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='pythonw.exe' OR Name='python.exe' OR Name='pyw.exe'")
For Each p In procs
    cmd = p.CommandLine
    If Not IsNull(cmd) Then
        cmd = LCase(cmd)
        If InStr(cmd, "app.py") > 0 Or InStr(cmd, "reminder.py") > 0 Then
            p.Terminate()
            killed = killed + 1
        End If
    End If
Next
On Error GoTo 0

MsgBox "Autostart DISABLED. Stopped " & killed & " running instance(s). (You can still open the app anytime from its shortcut.)", 64, "Ebbinghaus"
