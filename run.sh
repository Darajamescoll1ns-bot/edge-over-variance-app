#!/usr/bin/env bash
# Local launcher for Edge Over Variance.
#   ./run.sh            -> install deps (first run), seed the markets desk + a
#                          demo session, start the web app at http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"

echo "→ Installing web-layer dependencies (engine itself needs none)…"
python3 -m pip install --quiet -r requirements.txt

echo "→ Seeding the markets desk…"
python3 -c "import news_desk; news_desk.save_desk(news_desk.build_default_desk(), news_desk.DEFAULT_DATE); print('  desk ready')"

echo "→ Seeding a demo session so the dashboard isn't empty…"
python3 seed_demo.py || true

echo "→ Starting server at http://localhost:8000  (Ctrl-C to stop)"
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
