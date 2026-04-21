; NSIS 自定义安装脚本
; 功能：开机自启、卸载旧版本、安装向导

!macro customHeader
  ; 应用名称和 GUID
  !define APP_NAME "科技馆数字人讲解系统"
  !define APP_GUID "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}}"
  !define REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
  !define RUN_KEY "Software\Microsoft\Windows\CurrentVersion\Run"
!macroend

!macro preInit
  ; 安装前检查并卸载旧版本
  ReadRegStr $0 HKLM "${REG_KEY}" "UninstallString"
  ${If} $0 != ""
    ; 找到旧版本，静默卸载
    ExecWait '"$0" /S'
  ${EndIf}
!macroend

!macro customInstall
  ; 清理旧版本可能残留的 HKCU 自启项（避免重复启动）
  DeleteRegValue HKCU "${RUN_KEY}" "${APP_NAME}"
  
  ; 创建延迟启动脚本（VBScript，不弹窗，不易被安全软件拦截）
  ; 延迟 60 秒启动，给系统足够时间完成初始化
  FileOpen $0 "$INSTDIR\delayed-start.vbs" w
  FileWrite $0 'WScript.Sleep 60000$\r$\n'
  FileWrite $0 'Set WshShell = CreateObject("WScript.Shell")$\r$\n'
  FileWrite $0 'WshShell.Run """$INSTDIR\${APP_NAME}.exe""", 1, False$\r$\n'
  FileClose $0
  
  ; 写入开机自启注册表（HKLM 系统级，用户无法关闭）
  ; 使用 wscript 静默运行延迟启动脚本，避免 cmd 黑窗口和安全软件拦截
  WriteRegStr HKLM "${RUN_KEY}" "${APP_NAME}" 'wscript.exe "$INSTDIR\delayed-start.vbs"'
  
  ; 写入卸载信息
  WriteRegStr HKLM "${REG_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${REG_KEY}" "UninstallString" '"$INSTDIR\Uninstall ${APP_NAME}.exe"'
  WriteRegStr HKLM "${REG_KEY}" "DisplayIcon" '"$INSTDIR\${APP_NAME}.exe"'
  WriteRegStr HKLM "${REG_KEY}" "Publisher" "Science Museum"
  WriteRegStr HKLM "${REG_KEY}" "InstallLocation" "$INSTDIR"
!macroend

!macro customUnInstall
  ; 删除开机自启注册表
  DeleteRegValue HKLM "${RUN_KEY}" "${APP_NAME}"
  
  ; 删除延迟启动脚本
  Delete "$INSTDIR\delayed-start.vbs"
  
  ; 删除卸载信息
  DeleteRegKey HKLM "${REG_KEY}"
!macroend

!macro customRemoveFiles
  ; 保留用户数据目录
  ; 用户数据在 %APPDATA%/science-museum-digital-human/ 不受卸载影响
!macroend
