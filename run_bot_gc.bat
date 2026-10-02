cd /d "%~dp0"
set VESTBOT_ENV_FILE=.env.gc
call .venv\Scripts\activate.bat
python -m vestbot >> bot_gc.log 2>&1
