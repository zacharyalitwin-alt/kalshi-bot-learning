@echo off
REM start_recorder.bat - runs tools\record_v3.py and restarts it if it stops.
REM Keep this file in the Kalshi Bot Learning folder (next to tools and data).
REM Double-click it, or run it from Command Prompt. Close the window to stop.
title KXBTC15M recorder (auto-restart)
cd /d "%~dp0tools"
if not exist record_v3.py (
  echo Cannot find tools\record_v3.py next to this file.
  pause
  exit /b 1
)
:loop
echo [%date% %time%] starting recorder
python record_v3.py
>> "..\data\live\recorder_restarts.txt" echo [%date% %time%] recorder stopped, exit code %errorlevel%
echo Recorder stopped. Restarting in 10 seconds. Close this window to stop for good.
timeout /t 10 /nobreak >nul
goto loop
