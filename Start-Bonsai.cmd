@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start.ps1" -Model Bonsai %*
if errorlevel 1 pause
