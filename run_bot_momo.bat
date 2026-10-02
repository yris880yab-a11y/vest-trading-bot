cd /d "%~dp0"
set VESTBOT_ENV_FILE=.env.momo
call .venv\Scripts\activate.bat
python -m vestbot >> bot_momo.log 2>&1
