@echo off
REM =====================================================================
REM Start local MySQL without administrator rights (.cmd wrapper)
REM ---------------------------------------------------------------------
REM Why this wrapper:
REM   Running start_mysql.ps1 directly is blocked by the PowerShell
REM   execution policy with:
REM     AuthorizationManager check failed (PSSecurityException)
REM   This wrapper bypasses the policy for this single call only;
REM   it does not change any system-wide policy.
REM
REM Data dir : D:\ChatGPT\mysql_data   (moved off C: to free space)
REM Temp dir : D:\ChatGPT\mysql_tmp    (keep sort spills off C: too)
REM Usage    : double-click, or run  start_mysql.cmd
REM Stop     : Ctrl+C in this window, or
REM            mysqladmin -u root -h 127.0.0.1 -P 3306 shutdown
REM
REM NOTE: keep this file ASCII-only with CRLF line endings.
REM       cmd.exe misparses LF-only files and non-ANSI comments.
REM =====================================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_mysql.ps1"

if errorlevel 1 (
    echo.
    echo [FAILED] See D:\ChatGPT\mysql_data\mysql-error.log
    pause
)
