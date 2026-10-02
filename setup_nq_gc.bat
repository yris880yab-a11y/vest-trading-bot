@echo off
cd /d "%~dp0"
echo === Cai dat bot NQ + GC (lam 1 lan) ===
if not exist .venv ( py -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
echo.
set SIZE=
set /p SIZE=Tai khoan Topstep 50K hay 100K? Go 50 hoac 100 roi Enter: 
set SUFFIX=
if "%SIZE%"=="100" set SUFFIX=.100k
if not exist .env.momo ( copy .env.topstep.momo%SUFFIX%.example .env.momo >nul )
if not exist .env.momo.gc ( copy .env.topstep.momo.gc%SUFFIX%.example .env.momo.gc >nul )
echo.
echo Dien TOPSTEP_USERNAME va TOPSTEP_API_KEY vao CA HAI file vua mo, roi Ctrl+S.
echo (Da co san .env.momo tu truoc thi khong ghi de - xoa file do neu muon tao lai.)
start notepad .env.momo
start notepad .env.momo.gc
pause
