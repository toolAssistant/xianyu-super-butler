#!/usr/bin/env sh
set -eu

mkdir -p /app/data /app/logs /app/backups /app/static/uploads/images

if [ "${ENABLE_VNC:-false}" = "true" ]; then
  cleanup_process() {
    process_pid=${1:-}
    if [ -z "$process_pid" ]; then
      return 0
    fi

    if kill -0 "$process_pid" 2>/dev/null; then
      kill "$process_pid" 2>/dev/null || true
    fi

    wait "$process_pid" 2>/dev/null || true
  }

  cleanup_vnc_stack() {
    cleanup_process "${python_pid:-}"
    cleanup_process "${x11vnc_pid:-}"
    cleanup_process "${fluxbox_pid:-}"
    cleanup_process "${xvfb_pid:-}"
  }

  trap 'cleanup_vnc_stack' EXIT INT TERM HUP

  for required_command in Xvfb fluxbox x11vnc; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
      printf '%s\n' "required command not found: $required_command" >&2
      exit 1
    fi
  done

  DISPLAY=${DISPLAY:-:99}
  VNC_SCREEN=${VNC_SCREEN:-1980x1024x24}
  VNC_PORT=${VNC_PORT:-5900}
  export DISPLAY VNC_SCREEN VNC_PORT

  display_number=${DISPLAY#:}
  display_number=${display_number%%.*}

  rm -f "/tmp/.X${display_number}-lock" "/tmp/.X11-unix/X${display_number}"

  Xvfb "$DISPLAY" -screen 0 "$VNC_SCREEN" -ac +extension RANDR </dev/null >/dev/null 2>&1 &
  xvfb_pid=$!
  sleep "${VNC_STARTUP_WAIT_SECONDS:-1}"
  if ! kill -0 "$xvfb_pid" 2>/dev/null; then
    printf '%s\n' "failed to start Xvfb" >&2
    exit 1
  fi

  fluxbox </dev/null >/dev/null 2>&1 &
  fluxbox_pid=$!
  sleep "${VNC_STARTUP_WAIT_SECONDS:-1}"
  if ! kill -0 "$fluxbox_pid" 2>/dev/null; then
    printf '%s\n' "failed to start fluxbox" >&2
    exit 1
  fi

  x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport "$VNC_PORT" -listen 0.0.0.0 </dev/null >/dev/null 2>&1 &
  x11vnc_pid=$!
  sleep "${VNC_STARTUP_WAIT_SECONDS:-1}"
  if ! kill -0 "$x11vnc_pid" 2>/dev/null; then
    printf '%s\n' "failed to start x11vnc" >&2
    exit 1
  fi

  /bin/sh -c 'exec python Start.py' &
  python_pid=$!
  if wait "$python_pid"; then
    python_status=0
  else
    python_status=$?
  fi

  trap - EXIT INT TERM HUP
  cleanup_vnc_stack
  exit "$python_status"
fi

exec python Start.py
