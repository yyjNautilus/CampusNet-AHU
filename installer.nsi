; ============================================================================
;  校园网自动连接助手 (CampusNet) — NSIS 安装包脚本
;  构建: makensis.exe /INPUTCHARSET UTF8 installer.nsi
;  产物: release\CampusNet-Setup-1.1.0.exe
;  特性: 按用户安装(免管理员/UAC) / 桌面快捷方式 / 开始菜单 / 可选开机自启
;        / 标准卸载程序(设置-应用 可见) / 升级时自动关闭旧实例
; ============================================================================

Unicode true

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinVer.nsh"

!define APP_NAME      "校园网自动连接助手"
!define APP_ID        "CampusNet"
!define APP_VERSION   "1.2.3"
!define APP_PUBLISHER "CampusNet"
!define APP_EXE       "CampusNet.exe"
!define APP_URL       ""
!define UNINST_KEY    "Software\Microsoft\Windows\CurrentVersion\Uninstall\CampusNet"

!define SRC_EXE       "dist_onedir\CampusNet\CampusNet.exe"
!define SRC_INTERNAL  "dist_onedir\CampusNet\_internal"
!define SRC_README    "README.md"
!define SRC_ICON      "app_icon.ico"
!define SRC_LICENSE   "LICENSE.txt"

Name "${APP_NAME} ${APP_VERSION}"
Caption "${APP_NAME} ${APP_VERSION} 安装程序"
BrandingText "${APP_NAME} ${APP_VERSION}"
OutFile "release\校园网自动连接助手_v${APP_VERSION}_安装包.exe"
; 全新安装默认放用户目录(免管理员); 本机已有 D:\my own special applications 时沿用该目录
!define USER_DIR      "$LOCALAPPDATA\Programs\CampusNet"
!define D_DIR         "D:\my own special applications\CampusNet"
InstallDir "${USER_DIR}"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails show
ShowUnInstDetails show

VIProductVersion "1.2.3.0"
VIAddVersionKey /LANG=2052 "ProductName"     "${APP_NAME}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion"     "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "CompanyName"     "${APP_PUBLISHER}"
VIAddVersionKey /LANG=2052 "LegalCopyright"  ""

; ---------------------------------------------------------------- 界面 ----
!define MUI_ABORTWARNING
!define MUI_ICON   "${SRC_ICON}"
!define MUI_UNICON "${SRC_ICON}"

!define MUI_WELCOMEPAGE_TITLE "欢迎安装 ${APP_NAME} ${APP_VERSION}"
!define MUI_WELCOMEPAGE_TEXT "本程序用于解决每天手动打开网页输入校园网账号密码的麻烦。$\r$\n$\r$\n安装后它会：$\r$\n    - 开机自动在系统托盘待命，几乎不占内存$\r$\n    - 检测到校园网环境时，自动帮你完成网页门户(Portal)登录$\r$\n    - 不在校园网时完全不干预，交给 Windows 自己联网$\r$\n$\r$\n账号和密码使用 Windows 系统级加密(DPAPI)保存在本机，只有你的账户能解密，不会上传任何服务器。$\r$\n$\r$\n点[下一步]继续。"

!define MUI_DIRECTORYPAGE_TEXT_TOP "程序将安装到下面的文件夹。按用户安装，无需管理员权限；如需换位置可直接修改路径。"
!define MUI_DIRECTORYPAGE_TEXT_DESTINATION "安装到："

!define MUI_COMPONENTSPAGE_SMALLDESC
!define MUI_FINISHPAGE_TITLE "安装完成"
!define MUI_FINISHPAGE_TEXT "${APP_NAME} ${APP_VERSION} 已安装到你的电脑。$\r$\n$\r$\n首次使用请在打开的界面里填写「校园网账号」和「密码」，点[保存]即可。$\r$\n以后开机它会在右下角托盘静默驻留，无需再手动登录。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即打开 ${APP_NAME}(填写账号密码)"
!define MUI_FINISHPAGE_NOREBOOTSUPPORT

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${SRC_LICENSE}"
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE CheckWritable
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"

; ------------------------------------------------------------ 初始化 ----
Function .onInit
    SetShellVarContext current

    ; 全新安装时的默认目录: 本机已有 D:\my own special applications 就沿用, 否则放用户目录(免管理员)
    ReadRegStr $R0 HKCU "${UNINST_KEY}" "InstallLocation"
    ${If} $R0 == ""
        ${If} ${FileExists} "D:\my own special applications\*.*"
            StrCpy $INSTDIR "${D_DIR}"
        ${Else}
            StrCpy $INSTDIR "${USER_DIR}"
        ${EndIf}
    ${EndIf}

    ; 已安装过？提示是升级
    ReadRegStr $R0 HKCU "${UNINST_KEY}" "DisplayVersion"
    ${If} $R0 != ""
        ${IfNot} ${Silent}
            MessageBox MB_OKCANCEL|MB_ICONINFORMATION "检测到已安装 ${APP_NAME} v$R0，将继续升级到 v${APP_VERSION}。$\r$\n$\r$\n升级会保留你已保存的账号、密码和设置。$\r$\n$\r$\n点[确定]继续，点[取消]退出。" IDOK lbl_upgrade
            Abort
        ${EndIf}
        lbl_upgrade:
    ${EndIf}

    ; 关闭正在运行的旧实例，否则主程序文件被占用无法覆盖
    DetailPrint "正在关闭已运行的旧版本..."
    ReadRegStr $R0 HKCU "${UNINST_KEY}" "InstallLocation"
    ${If} $R0 != ""
        ${If} ${FileExists} "$R0\${APP_EXE}"
            ExecWait '"$R0\${APP_EXE}" --stop-all' $R1
        ${EndIf}
    ${EndIf}
    ${If} ${FileExists} "$INSTDIR\${APP_EXE}"
        ExecWait '"$INSTDIR\${APP_EXE}" --stop-all' $R1
    ${EndIf}
    ; 兜底：兼容没有 --stop-all 的旧版本
    nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM ${APP_EXE}'
    Pop $R1
    Sleep 300
FunctionEnd

; 选目录后先确认可写, 免得装到 Program Files 之类的受保护目录半路失败
Function CheckWritable
    ClearErrors
    CreateDirectory "$INSTDIR"
    IfErrors lbl_bad
    ClearErrors
    FileOpen $9 "$INSTDIR\.cn_write_test" w
    IfErrors lbl_bad
    FileClose $9
    Delete "$INSTDIR\.cn_write_test"
    Return

    lbl_bad:
    MessageBox MB_OK|MB_ICONEXCLAMATION "这个文件夹写不进去，通常是需要管理员权限（例如 C:\Program Files）。$\r$\n$\r$\n请换一个你自己有权限的位置，比如：$\r$\n    D:\Apps\CampusNet$\r$\n    C:\Users\你的用户名\Apps\CampusNet$\r$\n$\r$\n选好后点[下一步]继续。" IDOK
    Abort
FunctionEnd

; ------------------------------------------------------------ 安装 ----
Section "主程序 (必需)" SEC_MAIN
    SectionIn RO
    SetOutPath "$INSTDIR"
    SetOverwrite on

    ; 已有的 config.ini 不覆盖(升级保留账号密码)
    File "${SRC_EXE}"
    File /r "${SRC_INTERNAL}"
    File "${SRC_ICON}"
    File "${SRC_README}"

    ; 若安装包旁边带着用户自己的 config.ini，且目标还没有配置，则顺手导入
    ${IfNot} ${FileExists} "$INSTDIR\config.ini"
        ${If} ${FileExists} "$EXEDIR\config.ini"
            CopyFiles /SILENT "$EXEDIR\config.ini" "$INSTDIR\config.ini"
            DetailPrint "已导入安装包旁的配置: config.ini"
        ${EndIf}
    ${EndIf}

    DetailPrint "正在注册卸载信息..."
SectionEnd

Section "桌面快捷方式" SEC_DESKTOP
    SetShellVarContext current
    CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${SRC_ICON}" 0
    DetailPrint "已创建桌面快捷方式"
SectionEnd

Section "开始菜单快捷方式" SEC_STARTMENU
    SetShellVarContext current
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${SRC_ICON}" 0
    CreateShortCut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\Uninstall.exe" 0
    DetailPrint "已创建开始菜单快捷方式"
SectionEnd

Section "开机自动启动 (推荐)" SEC_AUTOSTART
    DetailPrint "正在设置开机自动启动..."
    ExecWait '"$INSTDIR\${APP_EXE}" --install' $R0
    ${If} $R0 != 0
        DetailPrint "开机自启设置返回码 $R0 — 可稍后在程序界面里点[开机自启]再试一次。"
    ${Else}
        DetailPrint "开机自动启动已设置(登录后自动在托盘待命)"
    ${EndIf}
SectionEnd

Section -Post
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    WriteRegStr HKCU "${UNINST_KEY}" "DisplayName"     "${APP_NAME}"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
    WriteRegStr HKCU "${UNINST_KEY}" "Publisher"       "${APP_PUBLISHER}"
    WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\${APP_EXE},0"
    WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$0"

    DetailPrint ""
    DetailPrint "安装目录: $INSTDIR"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SEC_MAIN} "程序主体与说明文档，必装。"
    !insertmacro MUI_DESCRIPTION_TEXT ${SEC_DESKTOP} "在桌面放一个图标，双击即可打开配置界面。"
    !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTMENU} "在开始菜单放一个文件夹，内含启动与卸载入口。"
    !insertmacro MUI_DESCRIPTION_TEXT ${SEC_AUTOSTART} "登录 Windows 后自动在右下角托盘静默运行，开机自动帮你登录校园网。"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ------------------------------------------------------------ 卸载 ----
Function un.onInit
    SetShellVarContext current

    ${If} ${Silent}
        StrCpy $R9 "1"
    ${Else}
        MessageBox MB_YESNO|MB_ICONQUESTION "卸载前请选择是否保留你保存的账号密码：$\r$\n$\r$\n[是] 一并删除配置(账号、密码、日志)，彻底清干净$\r$\n[否] 保留 config.ini，以后重装不用重新填写" IDYES lbl_del IDNO lbl_keep
        lbl_del:
            StrCpy $R9 "1"
            Goto lbl_done
        lbl_keep:
            StrCpy $R9 "0"
        lbl_done:
    ${EndIf}

    ; 先停掉托盘/后台实例，否则文件被占用删不掉
    DetailPrint "正在关闭运行中的实例..."
    ${If} ${FileExists} "$INSTDIR\${APP_EXE}"
        ExecWait '"$INSTDIR\${APP_EXE}" --stop-all' $R1
        ExecWait '"$INSTDIR\${APP_EXE}" --uninstall' $R1
    ${EndIf}
    nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM ${APP_EXE}'
    Pop $R1
    Sleep 300
FunctionEnd

Section "Uninstall"
    ; 1) 取消开机自启(任务计划 + 开机启动文件夹两种方式)
    ${If} ${FileExists} "$INSTDIR\${APP_EXE}"
        ExecWait '"$INSTDIR\${APP_EXE}" --uninstall' $R0
    ${EndIf}
    Delete "$SMSTARTUP\校园网自动连接.lnk"
    Delete "$SMSTARTUP\校园网自动连接.vbs"
    nsExec::Exec '"$SYSDIR\schtasks.exe" /Delete /TN CampusNetAutoLogin /F'
    Pop $R0

    ; 2) 快捷方式
    Delete "$DESKTOP\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\*.lnk"
    RMDir  "$SMPROGRAMS\${APP_NAME}"

    ; 3) 卸载注册表项
    DeleteRegKey HKCU "${UNINST_KEY}"

    ; 4) 程序文件
    RMDir /r "$INSTDIR\_internal"
    Delete "$INSTDIR\${APP_EXE}"
    Delete "$INSTDIR\${SRC_ICON}"
    Delete "$INSTDIR\README.md"
    Delete "$INSTDIR\campus_net.pid"
    Delete "$INSTDIR\campus_net.log"

    ${If} $R9 == "1"
        Delete "$INSTDIR\config.ini"
        RMDir /r "$INSTDIR"
        DetailPrint "配置与程序文件已全部删除"
    ${Else}
        ; 保留配置：目录里只剩 config.ini (RMDir 非空会失败，属预期)
        RMDir "$INSTDIR"
        DetailPrint "已保留配置: $INSTDIR\config.ini"
    ${EndIf}
SectionEnd
