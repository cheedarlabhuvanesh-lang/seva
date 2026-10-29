@echo off
cd /d "%~dp0"
title Seva
echo.
echo   Seva - starting
echo.
if not exist ".env" (
  echo   STOP: .env is missing.
  echo   Copy your .env ^(the one with GNANI_API_KEY^) into this folder:
  echo   %~dp0
  echo.
  pause
  exit /b 1
)
python -c "import flask" 2>nul || (
  echo   First run - installing what Seva needs, about a minute...
  python -m pip install -r requirements.txt
)
echo.
echo   Open this in Chrome:   http://localhost:8000/app/
echo   Leave this window open. Close it to stop Seva.
echo.
start "" http://localhost:8000/app/
python -m app.server --port 8000
pause
