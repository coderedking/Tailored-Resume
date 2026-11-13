@echo off
cd /d %~dp0backend
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r requirements.txt
pip install -q -r requirements-optional.txt >nul 2>&1
echo Open http://127.0.0.1:8000  (phone: http://YOUR-PC-IP:8000 on same Wi-Fi)
uvicorn app.main:app --host 0.0.0.0 --port 8000
