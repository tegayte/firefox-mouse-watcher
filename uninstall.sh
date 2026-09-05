#!/usr/bin/env bash
set -euo pipefail

GNOME_UUID="mouse-jiggler@murod"
GNOME_DEST="$HOME/.local/share/gnome-shell/extensions/$GNOME_UUID"

WATCHER_DEST="$HOME/firefox-watcher"

NATIVE_HOST_NAME="com.local.native_tab_switcher"
NATIVE_HOST_DEST="$HOME/.mozilla/native-messaging-hosts/$NATIVE_HOST_NAME.json"

echo "======================================"
echo " Firefox Mouse Watcher — uninstaller"
echo "======================================"
echo

echo "[1/4] Stopping watcher..."

if [[ -x "$WATCHER_DEST/stop-watcher.sh" ]]; then
    "$WATCHER_DEST/stop-watcher.sh" || true
fi

echo "[2/4] Removing watcher..."

if [[ -d "$WATCHER_DEST" ]]; then
    rm -rf "$WATCHER_DEST"
    echo "      Removed: $WATCHER_DEST"
else
    echo "      Nothing to remove."
fi

echo "[3/4] Removing GNOME extension..."

if [[ -d "$GNOME_DEST" ]]; then
    rm -rf "$GNOME_DEST"
    echo "      Removed: $GNOME_DEST"
else
    echo "      Nothing to remove."
fi

echo "[4/4] Removing Native Messaging manifest..."

if [[ -f "$NATIVE_HOST_DEST" ]]; then
    rm -f "$NATIVE_HOST_DEST"
    echo "      Removed: $NATIVE_HOST_DEST"
else
    echo "      Nothing to remove."
fi

echo
echo "Uninstallation complete."
