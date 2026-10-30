Set WshShell = CreateObject("WScript.Shell")
strCurrentDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strCurrentDir

' تشغيل السيرفر في الخلفية تماماً بدون ظهور نافذة موجه الأوامر السوداء
WshShell.Run "cmd /c RUN.bat", 0, False
