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
  
  ; 写入开机自启注册表（HKLM 系统级）
  ; 目录模式无需解压，可以直接启动，不需要延迟
  WriteRegStr HKLM "${RUN_KEY}" "${APP_NAME}" '"$INSTDIR\${APP_NAME}.exe"'
  
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
  
  ; 删除卸载信息
  DeleteRegKey HKLM "${REG_KEY}"
!macroend

!macro customRemoveFiles
  ; 保留用户数据目录
  ; 用户数据在 %APPDATA%/science-museum-digital-human/ 不受卸载影响
!macroend
