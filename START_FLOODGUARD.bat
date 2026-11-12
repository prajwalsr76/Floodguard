@echo off
rem FloodGuard live service: reads the CWC gauges and rainfall every 30 minutes,
rem runs the models, sends alerts and serves the dashboard at http://localhost:8765
title FloodGuard live service - keep this window open (you can minimise it)
cd /d "%~dp0"
set "PY="
if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set "PY=%USERPROFILE%\miniconda3\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\anaconda3\python.exe" set "PY=%LOCALAPPDATA%\anaconda3\python.exe"
if not defined PY if exist "%ProgramData%\anaconda3\python.exe" set "PY=%ProgramData%\anaconda3\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo Python was not found. Install Python 3.8 or newer from python.org
  echo ^(tick "Add python.exe to PATH"^), then double-click this file again.
  pause
  exit /b 1
)
echo Starting FloodGuard with "%PY%" ...
"%PY%" scripts\live\floodguard_server.py %*
echo.
echo FloodGuard has stopped. Read the message above, then press a key to close this window.
pause >nul
