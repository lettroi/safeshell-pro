@echo off
rem SafeShell Pro - keep ONE resident WSL terminal window alive.
rem
rem Why: WSL2 shuts its VM down shortly after the last session exits. Our
rem container uses host networking (required because WSL native dockerd bridge
rem DNS cannot reach model vendors), so a VM restart makes Windows see 502.
rem This window keeps the VM, dockerd and the container up, and self-heals
rem every 30s. Minimize it; do NOT close it while you use the service.
rem
rem Double-click this file, or add a shortcut to it in:
rem   shell:startup   (Win+R)  -> auto-start at logon.

setlocal
title SafeShell Pro - WSL keepalive (minimize me, do not close)

where wsl >nul 2>&1
if errorlevel 1 (
  echo [ERROR] WSL not found.
  echo Press any key to close...
  pause >nul
  exit /b 1
)

set "SFP_WIN_DIR=%~dp0"
if defined WSLENV (set "WSLENV=%WSLENV%:SFP_WIN_DIR/p") else (set "WSLENV=SFP_WIN_DIR/p")

rem -l is required: WSL auto-starts dockerd from /etc/profile.d on login shells.
wsl -e bash -lc "cd $SFP_WIN_DIR && bash keepalive.sh"

echo.
echo [INFO] keepalive ended. Press any key to close...
pause >nul
