@echo off
REM =====================================================================
REM Start the Vue 3 frontend dev server on http://127.0.0.1:5173
REM ---------------------------------------------------------------------
REM Prerequisites:
REM   1. MySQL running      -> start_mysql.cmd
REM   2. FastAPI running    -> start_api.cmd
REM   3. npm install done   -> cd frontend ^&^& npm install
REM
REM The dev server proxies /api/* to http://127.0.0.1:8000
REM (see frontend/vite.config.js), so no CORS setup is needed.
REM
REM NOTE: keep this file ASCII-only with CRLF line endings.
REM =====================================================================

cd /d "%~dp0frontend"
if not exist node_modules (
    echo [INFO] node_modules missing, running npm install...
    call npm install --no-audit --no-fund
)
call npm run dev

if errorlevel 1 (
    echo.
    echo [FAILED] Make sure start_api.cmd is running first.
    pause
)
