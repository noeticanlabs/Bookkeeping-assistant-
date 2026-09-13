#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.12+ is required. Install Python, python3-venv, and python3-pip, then rerun this script."
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit(f"Python 3.12+ is required; found {sys.version.split()[0]}")
print(f"Using Python {sys.version.split()[0]}")
PY

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo
echo "Installation complete."
echo "Start Bookkeeper Assistant with:"
echo "  source .venv/bin/activate"
echo "  export BOOKKEEPER_SECRET='replace-with-a-long-random-secret'"
echo "  python secure_web_app.py"
echo
echo "Optional document extraction:"
echo "  export OPENAI_API_KEY='your-key'"
echo "  export BOOKKEEPER_DOCUMENT_MODEL='gpt-5.6-luna'  # optional"
