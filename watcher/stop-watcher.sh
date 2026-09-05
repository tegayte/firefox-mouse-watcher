#!/usr/bin/env bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

pkill -TERM -f "$SCRIPT_DIR/watcher.py"
