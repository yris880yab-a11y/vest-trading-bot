@echo off
cd /d "%~dp0"
echo Bat 2 bot: NQ (bot_momo.log) va GC (bot_momo_gc.log). Dong cua so bot = dung bot.
type nul >> bot_momo.log
type nul >> bot_momo_gc.log
start "BOT NQ - momentum" /min cmd /c run_bot_momo.bat
start "BOT GC - momentum" /min cmd /c run_bot_momo_gc.bat
timeout /t 5 >nul
start "LOG NQ" powershell -NoExit -Command "Get-Content bot_momo.log -Wait -Tail 30"
start "LOG GC" powershell -NoExit -Command "Get-Content bot_momo_gc.log -Wait -Tail 30"
