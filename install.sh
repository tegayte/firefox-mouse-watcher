#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$HOME"

GNOME_UUID="mouse-jiggler@murod"
GNOME_DEST="$HOME_DIR/.local/share/gnome-shell/extensions/$GNOME_UUID"

WATCHER_DEST="$HOME_DIR/firefox-watcher"
NATIVE_HOST_DEST_DIR="$WATCHER_DEST/native-host"

NATIVE_HOST_NAME="com.local.native_tab_switcher"
NATIVE_HOST_DIR="$HOME_DIR/.mozilla/native-messaging-hosts"
NATIVE_HOST_MANIFEST="$NATIVE_HOST_DIR/$NATIVE_HOST_NAME.json"

echo "======================================"
echo " Firefox Mouse Watcher — installer"
echo "======================================"
echo
echo "Project: $PROJECT_DIR"
echo "Home:    $HOME_DIR"
echo

# ------------------------------------------------------------
# Check required files
# ------------------------------------------------------------

required_files=(
    "$PROJECT_DIR/gnome-extension/extension.js"
    "$PROJECT_DIR/gnome-extension/metadata.json"

    "$PROJECT_DIR/watcher/watcher.py"
    "$PROJECT_DIR/watcher/start-watcher.sh"
    "$PROJECT_DIR/watcher/stop-watcher.sh"

    "$PROJECT_DIR/firefox/extension/manifest.json"
    "$PROJECT_DIR/firefox/extension/background.js"

    "$PROJECT_DIR/firefox/native-host/nm_host.py"
    "$PROJECT_DIR/firefox/native-host/test_host.py"
    "$PROJECT_DIR/firefox/native-host/native-host-manifest.json"
)

echo "[1/7] Checking project files..."

for file in "${required_files[@]}"; do
    if [[ ! -f "$file" ]]; then
        echo "ERROR: missing file:"
        echo "  $file"
        exit 1
    fi
done

echo "      OK"

# ------------------------------------------------------------
# Check dependencies
# ------------------------------------------------------------

echo "[2/7] Checking dependencies..."

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is not installed."
    exit 1
fi

if ! command -v inotifywait >/dev/null 2>&1; then
    echo
    echo "ERROR: inotifywait is not installed."
    echo
    echo "Install it with:"
    echo "  sudo dnf install inotify-tools"
    echo
    exit 1
fi

echo "      python3:     $(command -v python3)"
echo "      inotifywait: $(command -v inotifywait)"

# ------------------------------------------------------------
# Install watcher + native host
# ------------------------------------------------------------

echo "[3/7] Installing watcher..."

mkdir -p "$WATCHER_DEST"
mkdir -p "$NATIVE_HOST_DEST_DIR"

cp "$PROJECT_DIR/watcher/watcher.py" \
   "$WATCHER_DEST/watcher.py"

cp "$PROJECT_DIR/watcher/start-watcher.sh" \
   "$WATCHER_DEST/start-watcher.sh"

cp "$PROJECT_DIR/watcher/stop-watcher.sh" \
   "$WATCHER_DEST/stop-watcher.sh"

cp "$PROJECT_DIR/firefox/native-host/nm_host.py" \
   "$NATIVE_HOST_DEST_DIR/nm_host.py"

chmod +x \
    "$WATCHER_DEST/watcher.py" \
    "$WATCHER_DEST/start-watcher.sh" \
    "$WATCHER_DEST/stop-watcher.sh" \
    "$NATIVE_HOST_DEST_DIR/nm_host.py"

echo "      Installed to:"
echo "      $WATCHER_DEST"
echo "      $NATIVE_HOST_DEST_DIR"

# ------------------------------------------------------------
# Install GNOME extension
# ------------------------------------------------------------

echo "[4/7] Installing GNOME extension..."

mkdir -p "$GNOME_DEST"

cp "$PROJECT_DIR/gnome-extension/extension.js" \
   "$GNOME_DEST/extension.js"

cp "$PROJECT_DIR/gnome-extension/metadata.json" \
   "$GNOME_DEST/metadata.json"

echo "      Installed to:"
echo "      $GNOME_DEST"

# ------------------------------------------------------------
# Install Firefox Native Messaging manifest
# ------------------------------------------------------------

echo "[5/7] Installing Firefox Native Messaging host..."

mkdir -p "$NATIVE_HOST_DIR"

python3 - "$PROJECT_DIR/firefox/native-host/native-host-manifest.json" \
         "$NATIVE_HOST_MANIFEST" \
         "$NATIVE_HOST_DEST_DIR/nm_host.py" <<'PY'
import json
import sys

template_path = sys.argv[1]
output_path = sys.argv[2]
host_path = sys.argv[3]

with open(template_path, "r", encoding="utf-8") as f:
    data = json.load(f)

data["path"] = host_path

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY

echo "      Manifest:"
echo "      $NATIVE_HOST_MANIFEST"

# ------------------------------------------------------------
# Verify installation
# ------------------------------------------------------------

echo "[6/7] Verifying installation..."

python3 - "$NATIVE_HOST_MANIFEST" "$NATIVE_HOST_DEST_DIR/nm_host.py" <<'PY'
import json
import os
import sys

manifest_path = sys.argv[1]
host_path = sys.argv[2]

with open(manifest_path, "r", encoding="utf-8") as f:
    data = json.load(f)

if data.get("name") != "com.local.native_tab_switcher":
    raise SystemExit("ERROR: invalid Native Messaging host name")

if data.get("type") != "stdio":
    raise SystemExit("ERROR: invalid Native Messaging host type")

if data.get("path") != host_path:
    raise SystemExit("ERROR: Native Messaging host path is incorrect")

if not os.path.isfile(host_path):
    raise SystemExit("ERROR: native host script does not exist")

if not os.access(host_path, os.X_OK):
    raise SystemExit("ERROR: native host is not executable")

allowed = data.get("allowed_extensions", [])

if "native-tab-switcher-poc@local.test" not in allowed:
    raise SystemExit(
        "ERROR: Firefox extension ID is missing from allowed_extensions"
    )

print("      Native Messaging manifest: OK")
print("      Native host: OK")
print("      Native host executable: OK")
PY

echo
echo "Installed files:"
echo "  $WATCHER_DEST/watcher.py"
echo "  $WATCHER_DEST/start-watcher.sh"
echo "  $WATCHER_DEST/stop-watcher.sh"
echo "  $WATCHER_DEST/native-host/nm_host.py"
echo "  $GNOME_DEST/extension.js"
echo "  $GNOME_DEST/metadata.json"
echo "  $NATIVE_HOST_MANIFEST"

# ------------------------------------------------------------
# Final
# ------------------------------------------------------------

echo
echo "[7/7] Installation complete."
echo
echo "======================================"
echo " IMPORTANT"
echo "======================================"
echo
echo "Firefox extension:"
echo "  about:debugging#/runtime/this-firefox"
echo
echo "Load:"
echo "  $PROJECT_DIR/firefox/extension/manifest.json"
echo
echo "GNOME extension:"
echo "  $GNOME_UUID"
echo
echo "Done."
