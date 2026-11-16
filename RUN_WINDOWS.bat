@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==============================================
echo    IssueFlow AI - Local dashboard
 echo ==============================================
if not exist .venv\Scripts\python.exe (
    echo [1/4] Creating Python environment...
    python -m venv .venv
    if errorlevel 1 goto :error
)
if not exist .env (
    echo [2/4] Copying environment configuration...
    copy .env.example .env >nul
)
echo [3/4] Installing Python packages...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :error
echo [4/4] Starting web server...
echo Open http://127.0.0.1:5000 in your browser.
.venv\Scripts\python.exe app.py
pause
exit /b 0
:error
echo.
echo Installation/start failed. Check Python and the messages above.
pause
exit /b 1
