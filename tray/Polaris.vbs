' Polaris tepsi uygulamasini konsol penceresi olmadan baslatir (Polaris.exe yoksa yedek).
' Bu dosya <kok>\tray\Polaris.vbs konumunda olmali; .venv <kok>\.venv altinda aranir.
Option Explicit
Dim fso, shell, trayDir, root, pyw
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
trayDir = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(trayDir)
pyw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
  MsgBox "Python ortami bulunamadi:" & vbCrLf & pyw & vbCrLf & vbCrLf & _
         "Once scripts\setup.ps1 ile kurulumu yapin.", vbExclamation, "Polaris"
  WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run """" & pyw & """ """ & trayDir & "\launch.pyw""", 0, False
