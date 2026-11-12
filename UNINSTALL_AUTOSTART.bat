@echo off
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\FloodGuard.bat" del "%STARTUP%\FloodGuard.bat"
echo FloodGuard will no longer start by itself. You can still start it with START_FLOODGUARD.bat
pause
