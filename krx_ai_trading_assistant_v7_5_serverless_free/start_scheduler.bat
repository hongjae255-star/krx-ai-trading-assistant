@echo off
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo [ERROR] .venv not found. Follow README installation first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe run.py scheduler
pause
