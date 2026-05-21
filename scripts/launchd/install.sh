#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE_PATH="$REPO_ROOT/launchd/com.xianyu.superbutler.plist.template"
LABEL="com.xianyu.superbutler"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
LOG_DIR="$REPO_ROOT/logs"
PYTHON_BIN="$(command -v python3 || command -v python)"
PATH_VALUE="${PATH:-/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin}"

if [[ ! -f "$TEMPLATE_PATH" ]]; then
  echo "模板文件不存在: $TEMPLATE_PATH" >&2
  exit 1
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "未找到 python3 或 python" >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

sed \
  -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
  -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
  -e "s|__PATH__|$PATH_VALUE|g" \
  "$TEMPLATE_PATH" > "$PLIST_PATH"

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "已安装并启动: $LABEL"
echo "plist: $PLIST_PATH"
echo "python: $PYTHON_BIN"
echo "仓库: $REPO_ROOT"
echo "日志: $LOG_DIR/launchd.stdout.log / $LOG_DIR/launchd.stderr.log"

