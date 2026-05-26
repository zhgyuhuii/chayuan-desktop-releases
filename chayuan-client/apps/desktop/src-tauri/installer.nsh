; ====================================================================
; NSIS 安装钩子: 把默认 ASCII 快捷方式改成中文「察元AI」
;
; 桌面图标重复的根因(2026-05-10 终于定位)
; -----------------------------------------
; Tauri 2 的 NSIS 模板桌面快捷方式不是在 Section Install 里创建,而是
; 通过 MUI_PAGE_FINISH 完成页的"Show Readme 复选框"回调创建:
;
;     !define MUI_FINISHPAGE_SHOWREADME
;     !define MUI_FINISHPAGE_SHOWREADME_TEXT  "$(createDesktop)"
;     !define MUI_FINISHPAGE_SHOWREADME_FUNCTION CreateOrUpdateDesktopShortcut
;
; NSIS_HOOK_POSTINSTALL 在 Section Install 末尾就触发 — 比 Finish 页**早**。
; 之前我们在 POSTINSTALL 里 Delete + CreateShortCut("察元AI.lnk"),Finish 页
; 随后又跑 CreateShortcut("Chayuan.lnk"),所以桌面**最终有两个图标**。
;
; 解决方案:用 MUI_PAGE_CUSTOMFUNCTION_LEAVE 注册 Finish 页离开回调,
; 时机在 SHOWREADME_FUNCTION 之后,把刚创建的 ${PRODUCTNAME}.lnk 直接 rename
; 成「察元AI.lnk」。这样既不会丢图标(用户勾了的话),也不会多一个英文图标
; (用户没勾就根本没创建,rename no-op,什么都不发生)。
;
; 开始菜单的快捷方式由 Section Install 的 CreateOrUpdateStartMenuShortcut
; 创建,在 POSTINSTALL **之前**触发,所以开始菜单的 rename 仍然放在
; POSTINSTALL 里走。
;
; 注意
; ----
; 1. 本文件**必须以 UTF-8 BOM 保存**,Tauri 的 Unicode NSIS 才能正确解析中文。
; 2. Add/Remove Programs 里的应用名仍是 "Chayuan"(注册表 DisplayName 由
;    Tauri 用 productName 直写),无关紧要,卸载流程不受影响。
; 3. 之前 Delete 一长串候选名的策略不再需要 — Tauri 只创建 ${PRODUCTNAME}.lnk
;    一种命名,rename 即可。
; ====================================================================

; --- Finish-page 离开回调注册 ---
; ``MUI_PAGE_CUSTOMFUNCTION_LEAVE`` 必须在 ``!insertmacro MUI_PAGE_FINISH`` 之前
; !define;Tauri 模板里 finish 页的 !insertmacro 在 line 411,我们的 hook
; 在 line 28 被 !include,顺序绝对早,MUI 会拿到这个 LEAVE 回调。
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE Cy_FinishLeaveCleanup

Function Cy_FinishLeaveCleanup
  ; 用户在 Finish 页点 [Finish] 时触发。MUI 调用顺序:
  ;   1. SHOWREADME_FUNCTION(用户勾了"创建桌面图标"才调)
  ;      → CreateOrUpdateDesktopShortcut 创建 ${PRODUCTNAME}.lnk
  ;   2. RUN_FUNCTION(用户勾了"运行 ${PRODUCTNAME}"才调)
  ;   3. **本函数(LEAVE)** ← 这里
  ;
  ; 用户没勾"创建桌面图标"时,${PRODUCTNAME}.lnk 不存在,IfFileExists 跳过,
  ; 不强行创建中文图标,尊重用户选择。
  IfFileExists "$DESKTOP\${PRODUCTNAME}.lnk" 0 cy_finish_done
    ; 防 rename 因目标已存在(老安装残留)失败,先删
    Delete "$DESKTOP\察元AI.lnk"
    Rename "$DESKTOP\${PRODUCTNAME}.lnk" "$DESKTOP\察元AI.lnk"
  cy_finish_done:
FunctionEnd

!macro NSIS_HOOK_POSTINSTALL
  ; 开始菜单:Tauri Section Install 内部已调 CreateOrUpdateStartMenuShortcut,
  ; 此时 ${PRODUCTNAME}.lnk 已存在 — rename 成中文。
  ; 两种布局都覆盖:
  ;   ${STARTMENUFOLDER} 非空(常态):$SMPROGRAMS\${PRODUCTNAME}\${PRODUCTNAME}.lnk
  ;   ${STARTMENUFOLDER} 空(平铺):  $SMPROGRAMS\${PRODUCTNAME}.lnk
  IfFileExists "$SMPROGRAMS\${PRODUCTNAME}\${PRODUCTNAME}.lnk" 0 cy_sm_flat
    Delete "$SMPROGRAMS\${PRODUCTNAME}\察元AI.lnk"
    Rename "$SMPROGRAMS\${PRODUCTNAME}\${PRODUCTNAME}.lnk" "$SMPROGRAMS\${PRODUCTNAME}\察元AI.lnk"
    Goto cy_sm_done
  cy_sm_flat:
  IfFileExists "$SMPROGRAMS\${PRODUCTNAME}.lnk" 0 cy_sm_done
    Delete "$SMPROGRAMS\察元AI.lnk"
    Rename "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$SMPROGRAMS\察元AI.lnk"
  cy_sm_done:
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; 卸载清理:覆盖中文 + 英文两种命名 + 子菜单文件夹 + 历史候选名
  ; (老版本可能留下不同的 .lnk,统一删一遍)
  Delete "$DESKTOP\察元AI.lnk"
  Delete "$DESKTOP\察元.lnk"
  Delete "$DESKTOP\察元 AI.lnk"
  Delete "$DESKTOP\${PRODUCTNAME}.lnk"
  Delete "$DESKTOP\${MAINBINARYNAME}.lnk"
  Delete "$DESKTOP\Chayuan.lnk"
  Delete "$DESKTOP\chayuan.lnk"
  Delete "$DESKTOP\Chayuan AI.lnk"
  Delete "$DESKTOP\Chayuan-AI.lnk"
  Delete "$DESKTOP\chayuan-desktop.lnk"

  Delete "$SMPROGRAMS\${PRODUCTNAME}\察元AI.lnk"
  Delete "$SMPROGRAMS\${PRODUCTNAME}\${PRODUCTNAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCTNAME}\${MAINBINARYNAME}.lnk"
  RMDir  "$SMPROGRAMS\${PRODUCTNAME}"
  Delete "$SMPROGRAMS\察元AI\察元AI.lnk"
  RMDir  "$SMPROGRAMS\察元AI"
  Delete "$SMPROGRAMS\Chayuan\Chayuan.lnk"
  RMDir  "$SMPROGRAMS\Chayuan"
  Delete "$SMPROGRAMS\察元AI.lnk"
  Delete "$SMPROGRAMS\${PRODUCTNAME}.lnk"

  ; --- 清理"记忆的数据目录路径" ---
  ; 数据目录指针存 ``%APPDATA%\chayuan\desktop.json``;不清的话用户卸载重装后
  ; 首启动会自动复用旧路径,跳过 FirstRunSetup 向导。用户明确要求每次重装都
  ; 强制弹一次目录选择。**只删 desktop config 目录**,不动 CHAYUAN_ROOT 真实
  ; 数据目录(那个由用户在向导里自选,卸载完整保留)。
  RMDir /r "$APPDATA\chayuan"
!macroend
