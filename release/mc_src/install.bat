@echo off
rem SafeShell Pro one-click launcher for Windows (WSL2 + Docker).
rem Double-click = install/start. Or: install.bat status | logs | stop | restart | doctor
rem All text here is ASCII on purpose: this folder name contains non-ASCII chars,
rem and WSLENV /p converts the path without any codepage-sensitive string handling.
setlocal

where wsl >nul 2>&1
if errorlevel 1 (
  echo [ERROR] WSL not found. Install WSL2 + Docker, or run bash install.sh on Linux.
  echo See SafeShellPro-User-Manual.md for details.
  echo Press any key to close...
  pause >nul
  exit /b 1
)

wsl -e true >nul 2>&1
if errorlevel 1 (
  echo [ERROR] WSL is not ready. Run "wsl" once, then try again.
  echo Press any key to close...
  pause >nul
  exit /b 1
)

set "SFP_WIN_DIR=%~dp0"
if defined WSLENV (set "WSLENV=%WSLENV%:SFP_WIN_DIR/p") else (set "WSLENV=SFP_WIN_DIR/p")

rem -l is required: WSL auto-starts dockerd from /etc/profile.d on login shells.
wsl -e bash -lc "cd $SFP_WIN_DIR && bash install.sh %*"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [OK] Done. Open http://127.0.0.1:8772
) else (
  echo [ERROR] install.sh returned %RC%. Try: install.bat doctor
)
echo.
echo Press any key to close...
pause >nul
exit /b %RC%
