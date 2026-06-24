#!/bin/sh

set -u

is_true() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|on|ON)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

BACKEND_PID=""

stop_processes() {
  if [ -n "${BACKEND_PID}" ] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
}

wait_and_capture() {
  PID="$1"
  set +e
  wait "${PID}"
  WAIT_STATUS=$?
  set -e
}

trap 'stop_processes' INT TERM

PORT="${PORT:-7860}"
export PORT

echo "[entrypoint] starting backend on port ${PORT}"
uv run python -m backend.main &
BACKEND_PID=$!

set -e
while true; do
  if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
    wait_and_capture "${BACKEND_PID}"
    BACKEND_STATUS="${WAIT_STATUS}"
    stop_processes
    exit "${BACKEND_STATUS}"
  fi

  sleep 2
done
