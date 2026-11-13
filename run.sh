#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/backend"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
pip install -q -r requirements-optional.txt || echo "(optional PyMuPDF skipped - PDF reference styles will be less precise)"
echo "Open http://127.0.0.1:8000  (phone: http://YOUR-PC-IP:8000 on same Wi-Fi)"
uvicorn app.main:app --host 0.0.0.0 --port 8000
