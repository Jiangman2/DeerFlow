@echo off
rem Wrapper so the domain switch works under the default PowerShell execution
rem policy (unsigned .ps1 files are blocked on this machine).
rem
rem   scripts\switch-feishu-domain.cmd feishu
rem   scripts\switch-feishu-domain.cmd cnooc
setlocal

set "PS=powershell"
where pwsh >nul 2>nul
if not errorlevel 1 set "PS=pwsh"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0switch-feishu-domain.ps1" %*
exit /b %ERRORLEVEL%