#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "Starting VALKYRIE PROTOCOL Co-op Server on http://localhost:8000 ..."
exec python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
