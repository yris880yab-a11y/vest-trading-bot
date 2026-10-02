@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo === Kiem tra ket noi bot NQ ===
set VESTBOT_ENV_FILE=.env.momo
python scripts\check_topstep.py
echo.
echo === Kiem tra ket noi bot GC ===
set VESTBOT_ENV_FILE=.env.momo.gc
python scripts\check_topstep.py
pause
