@echo off
rem Start FloodGuard automatically every time you log in to Windows (no administrator rights needed).
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
(
  echo @echo off
  echo start "FloodGuard live service" /min cmd /c ""%~dp0START_FLOODGUARD.bat""
) > "%STARTUP%\FloodGuard.bat"
if exist "%STARTUP%\FloodGuard.bat" (
  echo FloodGuard will now start by itself when you log in.
  echo To undo this, double-click UNINSTALL_AUTOSTART.bat
) else (
  echo Could not write to the Startup folder: %STARTUP%
)
pause
