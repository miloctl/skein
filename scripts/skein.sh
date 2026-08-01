#!/usr/bin/env bash
# Skein lifecycle: start/stop/restart/status/logs detached, or dev in the
# foreground. State lives in .run/ (gitignored): one pidfile and one log per
# service.
#
#   ./skein.sh start          # detached; survives closing the terminal
#   ./skein.sh stop
#   ./skein.sh restart
#   ./skein.sh status
#   ./skein.sh logs [backend|frontend]
#   ./skein.sh dev            # both in the foreground, Ctrl-C stops them
#
# Ports follow the same env vars docker-compose uses.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
RUN="$ROOT/.run"
BACKEND_PORT="${SKEIN_BACKEND_PORT:-8000}"
FRONTEND_PORT="${SKEIN_FRONTEND_PORT:-3000}"
SERVICES=(backend frontend)

c_ok=$'\033[32m'; c_bad=$'\033[31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
[ -t 1 ] || { c_ok=""; c_bad=""; c_dim=""; c_off=""; }

die() { printf '%serror:%s %s\n' "$c_bad" "$c_off" "$*" >&2; exit 1; }

port_of() { [ "$1" = backend ] && echo "$BACKEND_PORT" || echo "$FRONTEND_PORT"; }

# Is anything LISTENing on this port? Deliberately not bash's /dev/tcp: under
# WSL2 a connect to a closed loopback port hangs instead of returning
# ECONNREFUSED, which wedges the whole script. ss reads the socket table and
# cannot block; the /dev/tcp fallback is timeout-guarded for the same reason.
port_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
  else
    timeout 1 bash -c "(exec 3<>/dev/tcp/127.0.0.1/$1)" 2>/dev/null
  fi
}

pidfile() { echo "$RUN/$1.pid"; }
logfile() { echo "$RUN/$1.log"; }

read_pid() {
  local f; f=$(pidfile "$1")
  [ -f "$f" ] || return 1
  local p; p=$(cat "$f" 2>/dev/null) || return 1
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null && echo "$p"
}

preflight() {
  [ -x backend/.venv/bin/uvicorn ] || die "backend venv missing — cd backend && uv venv .venv && uv pip install -e '.[dev]' --python .venv/bin/python"
  [ -d frontend/node_modules ] || die "frontend deps missing — cd frontend && npm ci"
}

start_one() {
  local name=$1 port cmd
  port=$(port_of "$name")
  if read_pid "$name" >/dev/null; then
    printf '  %s already running (pid %s)\n' "$name" "$(read_pid "$name")"
    return 0
  fi
  # a stale pidfile is fine, but something else on the port is not — starting
  # anyway would leave a service that looks up but is not ours
  if port_busy "$port"; then
    die "port $port is already in use by something else (wanted for $name)"
  fi
  rm -f "$(pidfile "$name")"
  case $name in
    backend)  cmd="exec .venv/bin/uvicorn app.main:app --port $BACKEND_PORT --reload" ;;
    frontend) cmd="exec npm run dev -- --port $FRONTEND_PORT" ;;
  esac
  # setsid so the service leads its own process group: uvicorn --reload and
  # next dev both fork children, and killing the group is the only way to
  # reliably take the whole tree down later.
  setsid bash -c "cd '$ROOT/$name' && $cmd" >>"$(logfile "$name")" 2>&1 </dev/null &
  echo $! >"$(pidfile "$name")"
  printf '  %s starting (pid %s, port %s)\n' "$name" "$!" "$port"
}

stop_one() {
  local name=$1 pid
  if ! pid=$(read_pid "$name"); then
    rm -f "$(pidfile "$name")"
    printf '  %s not running\n' "$name"
    return 0
  fi
  kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 40); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    printf '  %s killed (pid %s did not exit on TERM)\n' "$name" "$pid"
  else
    printf '  %s stopped\n' "$name"
  fi
  rm -f "$(pidfile "$name")"
}

wait_healthy() {
  local waited=0
  printf 'waiting for health'
  while [ "$waited" -lt 90 ]; do
    if curl -fsS --max-time 2 "http://localhost:$BACKEND_PORT/health" >/dev/null 2>&1 &&
       curl -fsS --max-time 2 -o /dev/null "http://localhost:$FRONTEND_PORT" 2>/dev/null; then
      printf '\n'
      return 0
    fi
    # if a service died on startup, stop waiting and say so
    for s in "${SERVICES[@]}"; do
      read_pid "$s" >/dev/null || { printf '\n'; die "$s exited during startup — ./skein.sh logs $s"; }
    done
    printf '.'
    sleep 1
    waited=$((waited + 1))
  done
  printf '\n'
  die "not healthy after ${waited}s — ./skein.sh logs"
}

cmd_start() {
  preflight
  mkdir -p "$RUN"
  printf 'starting skein\n'
  for s in "${SERVICES[@]}"; do start_one "$s"; done
  wait_healthy
  cmd_status
}

cmd_stop() {
  printf 'stopping skein\n'
  for s in "${SERVICES[@]}"; do stop_one "$s"; done
}

cmd_status() {
  local any=1
  for s in "${SERVICES[@]}"; do
    local port pid; port=$(port_of "$s")
    if pid=$(read_pid "$s"); then
      any=0
      printf '  %sup%s    %-9s pid %-8s http://localhost:%s\n' "$c_ok" "$c_off" "$s" "$pid" "$port"
    elif port_busy "$port"; then
      printf '  %s??%s    %-9s port %s busy, but not ours\n' "$c_bad" "$c_off" "$s" "$port"
    else
      printf '  %sdown%s  %-9s\n' "$c_bad" "$c_off" "$s"
    fi
  done
  local health
  if health=$(curl -fsS --max-time 2 "http://localhost:$BACKEND_PORT/health" 2>/dev/null); then
    printf '  %s%s%s\n' "$c_dim" "$(sed -n 's/.*"provider":"\([^"]*\)".*"model":"\([^"]*\)".*/provider \1 · model \2/p' <<<"$health")" "$c_off"
  fi
  return $any
}

cmd_logs() {
  local which=${1:-}
  mkdir -p "$RUN"
  if [ -n "$which" ]; then
    [ -f "$(logfile "$which")" ] || die "no log for '$which'"
    tail -n 60 -f "$(logfile "$which")"
  else
    touch "$RUN/backend.log" "$RUN/frontend.log"
    tail -n 30 -f "$RUN/backend.log" "$RUN/frontend.log"
  fi
}

cmd_dev() {
  preflight
  trap 'kill 0' EXIT
  (cd backend && .venv/bin/uvicorn app.main:app --port "$BACKEND_PORT" --reload) &
  (cd frontend && npm run dev -- --port "$FRONTEND_PORT") &
  wait
}

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  logs)    shift; cmd_logs "${1:-}" ;;
  dev)     cmd_dev ;;
  *)
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
