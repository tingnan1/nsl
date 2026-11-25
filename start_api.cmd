@echo off
REM =====================================================================
REM Start the FastAPI backend on http://127.0.0.1:8000
REM ---------------------------------------------------------------------
REM Prerequisite: MySQL must be running first (run start_mysql.cmd).
REM
REM Endpoints:
REM   http://127.0.0.1:8000/docs        Swagger UI
REM   http://127.0.0.1:8000/api/health  health check
REM
REM NOTE: keep this file ASCII-only with CRLF line endings.
REM       cmd.exe misparses LF-only files and non-ANSI comments.
REM =====================================================================

cd /d "%~dp0"
".venv\Scripts\python.exe" -m uvicorn api.main:app --host 127.0.0.1 --port 8000

if errorlevel 1 (
    echo.
    echo [FAILED] Check that MySQL is running: start_mysql.cmd
    pause
)
