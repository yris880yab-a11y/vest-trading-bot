@echo off
cd /d "%~dp0"
echo === Cai dat bot NQ + GC (lam 1 lan) ===
if not exist .venv ( py -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if not exist .env.momo ( copy .env.topstep.momo.example .env.momo >nul )
if not exist .env.momo.gc ( copy .env.topstep.momo.gc.example .env.momo.gc >nul )
echo.
echo Dien TOPSTEP_USERNAME va TOPSTEP_API_KEY vao CA HAI file vua mo, roi Ctrl+S.
start notepad .env.momo
start notepad .env.momo.gc
pause
