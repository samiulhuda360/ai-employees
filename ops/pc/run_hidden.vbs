' Run a command with no window at all (scheduled tasks would otherwise flash a console).
' Usage: wscript.exe run_hidden.vbs "C:\Python313\python.exe" "D:\path\script.py"
Dim sh, cmd, a
Set sh = CreateObject("WScript.Shell")
cmd = ""
For Each a In WScript.Arguments
  cmd = cmd & " """ & a & """"
Next
WScript.Quit sh.Run(Trim(cmd), 0, True)
