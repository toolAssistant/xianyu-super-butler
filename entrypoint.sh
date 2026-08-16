#!/usr/bin/env sh
set -eu

mkdir -p /app/data /app/logs /app/backups /app/static/uploads/images

if [ "${ENABLE_VNC:-false}" = "true" ]; then
  process_is_running() {
    process_pid=${1:-}
    if [ -z "$process_pid" ] || ! kill -0 "$process_pid" 2>/dev/null; then
      return 1
    fi

    process_state=$(ps -o stat= -p "$process_pid" 2>/dev/null) || return 1
    set -- $process_state
    case "${1:-}" in
      ''|Z*) return 1 ;;
      *) return 0 ;;
    esac
  }

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

  report_service_failure() {
    service_name=$1
    service_log=$2
    printf '%s\n' "failed to start $service_name" >&2
    if [ -r "$service_log" ]; then
      while IFS= read -r log_line; do
        printf '%s: %s\n' "$service_name" "$log_line" >&2
      done < "$service_log"
    fi
  }

  trap 'cleanup_vnc_stack' EXIT INT TERM HUP

  for required_command in Xvfb fluxbox x11vnc ps; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
      printf '%s\n' "required command not found: $required_command" >&2
      exit 1
    fi
  done

  DISPLAY=${DISPLAY:-:99}
  VNC_SCREEN=${VNC_SCREEN:-1980x1024x24}
  VNC_PORT=${VNC_PORT:-5900}
  VNC_AUTH_FILE=${VNC_AUTH_FILE:-/tmp/x11vnc.pass}
  VNC_PASSWORD_FILE=${VNC_PASSWORD_FILE:-}
  VNC_LOG_DIR=${VNC_LOG_DIR:-/tmp}
  VNC_STARTUP_WAIT_SECONDS=${VNC_STARTUP_WAIT_SECONDS:-1}
  VNC_WATCH_INTERVAL_SECONDS=${VNC_WATCH_INTERVAL_SECONDS:-1}
  export DISPLAY VNC_SCREEN VNC_PORT

  if [ -z "$VNC_PASSWORD_FILE" ] || [ ! -r "$VNC_PASSWORD_FILE" ]; then
    printf '%s\n' 'VNC password file is required and must be readable when ENABLE_VNC=true' >&2
    exit 1
  fi

  VNC_PASSWORD=''
  IFS= read -r VNC_PASSWORD < "$VNC_PASSWORD_FILE" || true
  if [ -z "$VNC_PASSWORD" ]; then
    printf '%s\n' 'VNC password file must not be empty' >&2
    exit 1
  fi

  mkdir -p "$VNC_LOG_DIR"
  umask 077
  if ! x11vnc -storepasswd "$VNC_PASSWORD" "$VNC_AUTH_FILE" >/dev/null 2>&1; then
    printf '%s\n' "failed to create VNC password file: $VNC_AUTH_FILE" >&2
    exit 1
  fi
  unset VNC_PASSWORD

  display_number=${DISPLAY#:}
  display_number=${display_number%%.*}

  rm -f "/tmp/.X${display_number}-lock" "/tmp/.X11-unix/X${display_number}"

  xvfb_log="$VNC_LOG_DIR/xvfb.log"
  fluxbox_log="$VNC_LOG_DIR/fluxbox.log"
  x11vnc_log="$VNC_LOG_DIR/x11vnc.log"

  Xvfb "$DISPLAY" -screen 0 "$VNC_SCREEN" -ac +extension RANDR </dev/null >"$xvfb_log" 2>&1 &
  xvfb_pid=$!
  sleep "$VNC_STARTUP_WAIT_SECONDS"
  if ! process_is_running "$xvfb_pid"; then
    report_service_failure Xvfb "$xvfb_log"
    exit 1
  fi

  fluxbox </dev/null >"$fluxbox_log" 2>&1 &
  fluxbox_pid=$!
  sleep "$VNC_STARTUP_WAIT_SECONDS"
  if ! process_is_running "$fluxbox_pid"; then
    report_service_failure fluxbox "$fluxbox_log"
    exit 1
  fi

  x11vnc -display "$DISPLAY" -forever -shared -rfbauth "$VNC_AUTH_FILE" \
    -rfbport "$VNC_PORT" -listen 0.0.0.0 </dev/null >"$x11vnc_log" 2>&1 &
  x11vnc_pid=$!
  sleep "$VNC_STARTUP_WAIT_SECONDS"
  if ! process_is_running "$x11vnc_pid"; then
    report_service_failure x11vnc "$x11vnc_log"
    exit 1
  fi

  /bin/sh -c 'exec python Start.py' &
  python_pid=$!

  python_status=0
  while process_is_running "$python_pid"; do
    for service_name in Xvfb fluxbox x11vnc; do
      case "$service_name" in
        Xvfb) service_pid=$xvfb_pid ;;
        fluxbox) service_pid=$fluxbox_pid ;;
        x11vnc) service_pid=$x11vnc_pid ;;
      esac
      if ! process_is_running "$service_pid"; then
        printf '%s\n' "$service_name exited unexpectedly" >&2
        python_status=1
        cleanup_process "$python_pid"
        break 2
      fi
    done
    sleep "$VNC_WATCH_INTERVAL_SECONDS"
  done

  if [ "$python_status" -eq 0 ]; then
    if wait "$python_pid"; then
      python_status=0
    else
      python_status=$?
    fi
  fi

  trap - EXIT INT TERM HUP
  cleanup_vnc_stack
  exit "$python_status"
fi

exec python Start.py
