; ============================================================================
; Chayuan Windows NSIS 安装器脚本
;
; 用法（在 Windows 构建机上）：
;     makensis /DCHAYUAN_VERSION=1.0.0.0 /DBUILD_ROOT=..\..\build\dist-win packaging\windows\Chayuan.nsi
;
; build_win.ps1 会在 build\dist-win\ 下布置好：
;     src\chayuan-server\          # 业务源码
;     dist\python-runtime.tar.gz   # python-build-standalone
;     dist\wheels\                 # 离线 whl
;     dist\requirements-runtime.txt
;     dist\first_run.ps1
;     bin\launcher.vbs
;     bin\launcher.ps1
;     AppIcon.ico
;
; 本脚本把这些原样拷进 $INSTDIR，加上快捷方式。
; ============================================================================

!include "MUI2.nsh"

!ifndef CHAYUAN_VERSION
    !define CHAYUAN_VERSION "0.0.0.0"
!endif
!ifndef BUILD_ROOT
    !define BUILD_ROOT "..\..\build\dist-win"
!endif

Name "Chayuan ${CHAYUAN_VERSION}"
OutFile "${BUILD_ROOT}\..\Chayuan-${CHAYUAN_VERSION}-windows-amd64-personal-dist.exe"
Unicode true
RequestExecutionLevel admin
InstallDir "$PROGRAMFILES64\Chayuan"
InstallDirRegKey HKLM "Software\Chayuan" "InstallDir"

VIProductVersion "${CHAYUAN_VERSION}"
VIAddVersionKey "ProductName" "Chayuan"
VIAddVersionKey "CompanyName" "北京智灵鸟科技中心"
VIAddVersionKey "FileVersion" "${CHAYUAN_VERSION}"
VIAddVersionKey "FileDescription" "察元 AI 助手（个人版）"

!define MUI_ABORTWARNING
!define MUI_ICON "${BUILD_ROOT}\AppIcon.ico"
!define MUI_UNICON "${BUILD_ROOT}\AppIcon.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Section "MainSection" SEC01
    SetOutPath "$INSTDIR"

    ; 图标 / 许可等根文件
    File "${BUILD_ROOT}\AppIcon.ico"

    ; 启动脚本 bin/
    SetOutPath "$INSTDIR\bin"
    File "${BUILD_ROOT}\bin\launcher.vbs"
    File "${BUILD_ROOT}\bin\launcher.ps1"

    ; 业务源码 src/chayuan-server/**
    SetOutPath "$INSTDIR\src\chayuan-server"
    File /r "${BUILD_ROOT}\src\chayuan-server\*.*"

    ; dist 资源 dist/**
    SetOutPath "$INSTDIR\dist"
    File "${BUILD_ROOT}\dist\python-runtime.tar.gz"
    File "${BUILD_ROOT}\dist\requirements-runtime.txt"
    File "${BUILD_ROOT}\dist\first_run.ps1"

    SetOutPath "$INSTDIR\dist\wheels"
    File /r "${BUILD_ROOT}\dist\wheels\*.*"

    ; 开始菜单快捷方式
    CreateDirectory "$SMPROGRAMS\Chayuan"
    CreateShortcut "$SMPROGRAMS\Chayuan\Chayuan.lnk" \
        "wscript.exe" "\"$INSTDIR\bin\launcher.vbs\"" \
        "$INSTDIR\AppIcon.ico" 0 SW_SHOWMINIMIZED
    CreateShortcut "$SMPROGRAMS\Chayuan\卸载 Chayuan.lnk" \
        "$INSTDIR\Uninstall.exe"

    ; 桌面快捷方式
    CreateShortcut "$DESKTOP\Chayuan.lnk" \
        "wscript.exe" "\"$INSTDIR\bin\launcher.vbs\"" \
        "$INSTDIR\AppIcon.ico" 0 SW_SHOWMINIMIZED

    ; 注册表：写 InstallDir + 卸载入口（控制面板可见）
    WriteRegStr HKLM "Software\Chayuan" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Chayuan" \
        "DisplayName" "Chayuan ${CHAYUAN_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Chayuan" \
        "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Chayuan" \
        "DisplayIcon" "$INSTDIR\AppIcon.ico"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Chayuan" \
        "Publisher" "北京智灵鸟科技中心"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Chayuan" \
        "DisplayVersion" "${CHAYUAN_VERSION}"

    WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
    ; 不删 ~\.chayuan\ ：用户数据 / 下载的 Python 由用户决定是否清理；
    ; 卸载脚本里给用户提示即可。
    MessageBox MB_YESNO "是否同时删除 Chayuan 的数据目录 %USERPROFILE%\.chayuan ？（含知识库 / 配置 / Python 运行时）" \
        IDNO skip_userdata
        RMDir /r "$PROFILE\.chayuan"
    skip_userdata:

    Delete "$INSTDIR\Uninstall.exe"
    Delete "$INSTDIR\AppIcon.ico"
    RMDir /r "$INSTDIR\bin"
    RMDir /r "$INSTDIR\src"
    RMDir /r "$INSTDIR\dist"
    RMDir "$INSTDIR"

    Delete "$DESKTOP\Chayuan.lnk"
    RMDir /r "$SMPROGRAMS\Chayuan"

    DeleteRegKey HKLM "Software\Chayuan"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Chayuan"
SectionEnd
