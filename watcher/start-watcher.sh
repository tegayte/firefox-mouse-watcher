#!/usr/bin/env bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

cd "$SCRIPT_DIR" || exit 1

if pgrep -f "$SCRIPT_DIR/watcher.py" >/dev/null; then
    exit 0
fi

exec /usr/bin/python3 "$SCRIPT_DIR/watcher.py"
