@echo off
cd /d "%~dp0"
set "needs_install=0"
if not exist "%~dp0.runtime\desktop\Scripts\pythonw.exe" set "needs_install=1"
if not exist "%~dp0.runtime\install-version" set "needs_install=1"
if exist "%~dp0.runtime\install-version" (
  findstr /x /c:"2.0.0" "%~dp0.runtime\install-version" >nul
  if errorlevel 1 set "needs_install=1"
)
if "%needs_install%"=="1" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Desktop.ps1"
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
start "" "%~dp0.runtime\desktop\Scripts\pythonw.exe" "%~dp0desktop.py"
