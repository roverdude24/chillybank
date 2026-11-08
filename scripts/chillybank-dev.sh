#!/bin/zsh
# Start the dev ChillyBank (this checkout, port 8081, book copy in ~/.chillybank-dev) if it
# is not running, then open its window. Used by "ChillyBank Dev.app" in this folder.
set -u
repo="${0:A:h:h}"
port=8081
if ! nc -z 127.0.0.1 $port 2>/dev/null; then
  mkdir -p "$HOME/.chillybank-dev/logs"
  cd "$repo" || exit 1
  nohup .venv/bin/python -m uvicorn backend.main:app --reload --reload-dir backend --port $port \
    >> "$HOME/.chillybank-dev/logs/dev-server.log" 2>&1 &
  for _ in {1..60}; do nc -z 127.0.0.1 $port 2>/dev/null && break; sleep 0.5; done
fi
open "$repo/ChillyBank Dev.app/Contents/Resources/ChillyBank Dev Window.app"
