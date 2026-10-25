; ============================================================
;  kport Windows NSIS Installer Script
;  Built with NSIS (https://nsis.sourceforge.io/)
;
;  Produces: kport-<VERSION>-setup.exe
;  Installs: kport.exe to $PROGRAMFILES64\kport\
;  Features: PATH registration, Start Menu, silent install (/S)
;
;  Build: makensis packaging\windows\installer.nsi
; ============================================================

Unicode True

; ---- Includes -----------------------------------------------
!include "MUI2.nsh"
!include "LogicLib.nsh"

; ---- Version (overridden by win_build.py via /DVERSION=x.y.z) ----
!ifndef VERSION
  !define VERSION "5.3.0"
!endif
!ifndef OUTFILE
  !define OUTFILE "..\..\dist\win\kport-${VERSION}-setup.exe"
!endif
!ifndef KPORT_EXE
  !define KPORT_EXE "..\..\dist\win\kport.exe"
!endif

; ---- Installer Metadata -------------------------------------
Name              "kport ${VERSION}"
OutFile           "${OUTFILE}"
InstallDir        "$PROGRAMFILES64\kport"
InstallDirRegKey  HKLM "Software\kport" "InstallDir"
RequestExecutionLevel admin
BrandingText      "kport — Cross-platform port inspector and killer"

; ---- MUI Settings -------------------------------------------
!define MUI_ABORTWARNING
!define MUI_ICON "..\..\assets\icons\windows\icon_256_256_ico.ico"
!define MUI_UNICON "..\..\assets\icons\windows\icon_256_256_ico.ico"
!define MUI_WELCOMEPAGE_TITLE "Install kport ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT  "kport is a cross-platform CLI tool to inspect and kill processes by port.$\r$\n$\r$\nClick Next to install kport ${VERSION}."
; NOTE: MUI_FINISHPAGE_RUN intentionally omitted — launching the exe after
; install causes issues in Chocolatey automated testing (silent /S mode).
!define MUI_FINISHPAGE_LINK     "GitHub: github.com/farman20ali/kport"
!define MUI_FINISHPAGE_LINK_LOCATION "https://github.com/farman20ali/kport"

; ---- Pages --------------------------------------------------
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ---- Sections -----------------------------------------------

Section "kport Core" SecCore
  SectionIn RO    ; Mandatory — cannot be deselected

  SetOutPath "$INSTDIR"
  File "${KPORT_EXE}"

  ; Write registry info (for uninstaller + Programs & Features)
  WriteRegStr HKLM "Software\kport" "InstallDir"   "$INSTDIR"
  WriteRegStr HKLM "Software\kport" "Version"      "${VERSION}"

  WriteRegStr   HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "DisplayName"          "kport ${VERSION}"
  WriteRegStr   HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "DisplayVersion"       "${VERSION}"
  WriteRegStr   HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "Publisher"            "Farman Ali"
  WriteRegStr   HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "UninstallString"      '"$INSTDIR\uninstall.exe"'
  WriteRegStr   HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "InstallLocation"      "$INSTDIR"
  WriteRegStr   HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "URLInfoAbout"         "https://github.com/farman20ali/kport"
  WriteRegStr   HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "DisplayIcon"          "$INSTDIR\kport.exe"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "NoModify"             1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "NoRepair"             1

  ; Estimate size (KB) — rough size of kport.exe bundle
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport" \
                "EstimatedSize"        15000

  WriteUninstaller "$INSTDIR\uninstall.exe"

SectionEnd

Section "Add to System PATH" SecPath
  ; We use PowerShell to safely update the PATH without hitting the 1024-character NSIS limit,
  ; which would truncate the user's PATH if it is long.
  DetailPrint "Adding $INSTDIR to system PATH..."
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$$instDir = $\'$INSTDIR$\'; $$path = [System.Environment]::GetEnvironmentVariable($\'PATH$\', $\'Machine$\'); $$paths = $$path -split $\';$\' | ForEach-Object { $$_.Trim() } | Where-Object { $$_ }; if ($$paths -notcontains $$instDir) { $$paths += $$instDir; [System.Environment]::SetEnvironmentVariable($\'PATH$\', ($$paths -join $\';$\'), $\'Machine$\') }"'
SectionEnd

Section "Start Menu Shortcut" SecStartMenu
  CreateDirectory "$SMPROGRAMS\kport"
  CreateShortcut  "$SMPROGRAMS\kport\kport.lnk" \
                  "$INSTDIR\kport.exe" \
                  "--version" \
                  "$INSTDIR\kport.exe" 0 \
                  SW_SHOWNORMAL ALT|F4 \
                  "kport — port inspector and killer"
  CreateShortcut  "$SMPROGRAMS\kport\Uninstall kport.lnk" \
                  "$INSTDIR\uninstall.exe"
SectionEnd

; ---- Uninstaller --------------------------------------------

Section "Uninstall"
  ; Remove from PATH
  DetailPrint "Removing $INSTDIR from system PATH..."
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$$instDir = $\'$INSTDIR$\'; $$path = [System.Environment]::GetEnvironmentVariable($\'PATH$\', $\'Machine$\'); $$paths = $$path -split $\';$\' | ForEach-Object { $$_.Trim() } | Where-Object { $$_ -and $$_ -ne $$instDir }; [System.Environment]::SetEnvironmentVariable($\'PATH$\', ($$paths -join $\';$\'), $\'Machine$\')"'

  ; Remove files
  Delete "$INSTDIR\kport.exe"
  Delete "$INSTDIR\uninstall.exe"
  RMDir  "$INSTDIR"

  ; Remove Start Menu
  Delete "$SMPROGRAMS\kport\kport.lnk"
  Delete "$SMPROGRAMS\kport\Uninstall kport.lnk"
  RMDir  "$SMPROGRAMS\kport"

  ; Remove registry entries
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\kport"
  DeleteRegKey HKLM "Software\kport"

SectionEnd

; ---- Section Descriptions -----------------------------------

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCore}      "kport.exe — the main executable (required)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecPath}      "Add kport to the System PATH so it works from any terminal"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecStartMenu} "Create Start Menu shortcuts for kport"
!insertmacro MUI_FUNCTION_DESCRIPTION_END
