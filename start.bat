@echo off
echo ========================================
echo   Voice Narrator Studio
echo ========================================
echo.

cd /d "%~dp0"
call venv\Scripts\activate.bat

echo Starting server on http://localhost:8765
echo Press Ctrl+C to stop
echo.

venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8765 --reload
pause
