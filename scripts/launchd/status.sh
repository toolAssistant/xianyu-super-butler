#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="com.xianyu.superbutler"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "label: $LABEL"
echo "plist: $PLIST_PATH"
echo
launchctl list | grep "$LABEL" || echo "launchctl 中未发现 $LABEL"
echo
lsof -nP -iTCP:8080 -sTCP:LISTEN || echo "8080 当前未监听"
echo
tail -n 40 "$REPO_ROOT/logs/launchd.stdout.log" 2>/dev/null || true
tail -n 40 "$REPO_ROOT/logs/launchd.stderr.log" 2>/dev/null || true
