@echo off
REM Tadawal robot: keeps the robot running; restarts it if it stops.
REM Double-click this file, or add it to Task Scheduler ("At log on").
cd /d "%~dp0\..\.."
:loop
python skills\auto-trader\robot.py run --verbose
echo Robot stopped. Restarting in 60 seconds (close this window to stop it for good)...
timeout /t 60
goto loop
