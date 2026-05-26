' ============================================================================
' Chayuan Windows 启动器（.vbs）
'
' 为什么不用 .bat？.bat 双击会弹出黑色 CMD 窗口，UX 差。WScript 可以
' 在"隐藏窗口"的情况下调起 pythonw.exe，完全静默——这是 Windows 桌面
' 工具的标准做法。
'
' 本脚本在 .exe 安装器双击启动 / 开始菜单点击 / 桌面快捷方式双击时执行。
' 工作流程：
'   1. 定位同级的 bin\ 目录；如果有 launcher.ps1 就调 PowerShell 执行；
'   2. 否则退回简单路径查找：%LOCALAPPDATA%\Chayuan\python\pythonw.exe。
'
' 错误处理：启动失败时弹一个消息框，不让用户陷入"点了没反应"的黑洞。
' ============================================================================

Option Explicit

Dim FSO, Shell, scriptDir, ps1Path, exitCode
Set FSO = CreateObject("Scripting.FileSystemObject")
Set Shell = CreateObject("WScript.Shell")

scriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
ps1Path   = scriptDir & "\launcher.ps1"

If Not FSO.FileExists(ps1Path) Then
    MsgBox "Chayuan 启动失败：找不到 launcher.ps1" & vbCrLf & _
           "位置：" & ps1Path, vbCritical, "Chayuan"
    WScript.Quit 1
End If

' 0 = 隐藏窗口；False = 不等待（异步）
exitCode = Shell.Run( _
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1Path & """", _
    0, False)
