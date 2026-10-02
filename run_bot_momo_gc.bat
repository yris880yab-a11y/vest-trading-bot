cd /d "%~dp0"
set VESTBOT_ENV_FILE=.env.momo.gc
call .venv\Scripts\activate.bat
python -m vestbot >> bot_momo_gc.log 2>&1
