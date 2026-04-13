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
GATEWAY_PID=""

stop_processes() {
  if [ -n "${GATEWAY_PID}" ] && kill -0 "${GATEWAY_PID}" 2>/dev/null; then
    kill "${GATEWAY_PID}" 2>/dev/null || true
  fi
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

if is_true "${WECHAT_INTEGRATION_ENABLED:-false}"; then
  : "${INTERNAL_GATEWAY_TOKEN:?INTERNAL_GATEWAY_TOKEN is required when WECHAT_INTEGRATION_ENABLED=true}"
  if [ -z "${API_BASE_URL:-}" ]; then
    API_BASE_URL="http://127.0.0.1:${PORT}"
    export API_BASE_URL
  fi
  echo "[entrypoint] starting im-gateway -> ${API_BASE_URL}"
  uv run python -m im_gateway.main &
  GATEWAY_PID=$!
else
  echo "[entrypoint] WECHAT_INTEGRATION_ENABLED is false; skip im-gateway"
fi

set -e
while true; do
  if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
    wait_and_capture "${BACKEND_PID}"
    BACKEND_STATUS="${WAIT_STATUS}"
    stop_processes
    if [ -n "${GATEWAY_PID}" ]; then
      wait "${GATEWAY_PID}" 2>/dev/null || true
    fi
    exit "${BACKEND_STATUS}"
  fi

  if [ -n "${GATEWAY_PID}" ] && ! kill -0 "${GATEWAY_PID}" 2>/dev/null; then
    wait_and_capture "${GATEWAY_PID}"
    GATEWAY_STATUS="${WAIT_STATUS}"
    echo "[entrypoint] im-gateway exited with status ${GATEWAY_STATUS}"
    stop_processes
    wait "${BACKEND_PID}" 2>/dev/null || true
    exit "${GATEWAY_STATUS}"
  fi

  sleep 2
done
