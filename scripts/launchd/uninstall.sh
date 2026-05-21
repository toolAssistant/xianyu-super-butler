#!/usr/bin/env bash
set -euo pipefail

LABEL="com.xianyu.superbutler"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"

echo "已卸载: $LABEL"

