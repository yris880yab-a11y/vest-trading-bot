cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m vestbot >> bot.log 2>&1
