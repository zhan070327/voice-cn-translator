Option Explicit

Dim fso, shell, projectDir, pythonwPath, mainPath, desktopPath, shortcutPath, shortcut
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwPath = fso.BuildPath(projectDir, ".venv\Scripts\pythonw.exe")
mainPath = fso.BuildPath(projectDir, "main.py")

If Not fso.FileExists(mainPath) Then
    MsgBox "Put this file in the same folder as main.py, then run it again.", vbExclamation, "Valorant Translator"
    WScript.Quit 1
End If

If Not fso.FileExists(pythonwPath) Then
    MsgBox "Run start_windows.bat once to install dependencies, then run this file again.", vbExclamation, "Valorant Translator"
    WScript.Quit 1
End If

desktopPath = shell.SpecialFolders("Desktop")
shortcutPath = fso.BuildPath(desktopPath, "Valorant Translator.lnk")
Set shortcut = shell.CreateShortcut(shortcutPath)
shortcut.TargetPath = pythonwPath
shortcut.Arguments = Chr(34) & mainPath & Chr(34)
shortcut.WorkingDirectory = projectDir
shortcut.IconLocation = pythonwPath & ",0"
shortcut.Description = "Valorant Translator"
shortcut.Save

MsgBox "Desktop shortcut created: Valorant Translator", vbInformation, "Valorant Translator"
