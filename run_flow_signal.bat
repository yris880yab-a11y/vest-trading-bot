cd /d "%~dp0"
set VESTBOT_ENV_FILE=.env.flow
call .venv\Scripts\activate.bat
REM Ghi lai du lieu de chay lai (--replay): them  --record data\nq_flow.jsonl  (NQ L2 rat nang, vai GB/ngay)
python -m vestbot.flow_signal
pause
