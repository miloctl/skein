#!/usr/bin/env bash
# Skein lifecycle. State lives in .run/ (gitignored): one pidfile and one log
# per service.
#
#   ./scripts/skein.sh start          # detached; survives closing the terminal
#   ./scripts/skein.sh stop
#   ./scripts/skein.sh restart
#   ./scripts/skein.sh status
#   ./scripts/skein.sh logs [backend|frontend]
#   ./scripts/skein.sh dev            # both in the foreground, Ctrl-C stops them
#
# Ports: SKEIN_BACKEND_PORT (default 8000), SKEIN_FRONTEND_PORT (default 3000).
# Note SKEIN_BACKEND_PORT is local to this script — docker-compose hardcodes
# 8000:8000 and only honours SKEIN_FRONTEND_PORT.
#
# Linux/WSL2 only: needs setsid for process groups and /proc for the pid-reuse
# guard. On macOS use `docker compose up` instead.
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

usage() {
  cat <<'EOF'
skein — run the app for local dev

  ./scripts/skein.sh start          detached; survives closing the terminal
  ./scripts/skein.sh stop
  ./scripts/skein.sh restart
  ./scripts/skein.sh status
  ./scripts/skein.sh logs [backend|frontend]
  ./scripts/skein.sh dev            foreground; Ctrl-C stops both

Ports: SKEIN_BACKEND_PORT (8000), SKEIN_FRONTEND_PORT (3000)
EOF
}

port_of() { [ "$1" = backend ] && echo "$BACKEND_PORT" || echo "$FRONTEND_PORT"; }
pidfile() { echo "$RUN/$1.pid"; }
logfile() { echo "$RUN/$1.log"; }

# Is anything LISTENing on this port? Deliberately not bash's /dev/tcp: under
# WSL2 a connect to a closed loopback port hangs instead of returning
# ECONNREFUSED, which wedges the script. ss reads the socket table and cannot
# block.
port_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
  else
    lsof -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
  fi
}

# Field 22 of /proc/PID/stat: process start time in clock ticks since boot.
# Unique per pid *instance*, so it distinguishes our service from whatever
# inherited the number after a reboot. The sub() is load-bearing — comm can
# contain spaces ("npm run dev --p"), which breaks naive field indexing.
starttime() { awk '{ sub(/^[0-9]+ \(.*\) /, ""); print $20 }' "/proc/$1/stat" 2>/dev/null; }

raw_pid() {
  local f; f=$(pidfile "$1")
  [ -f "$f" ] || return 1
  local p rest
  read -r p rest <"$f" 2>/dev/null || return 1
  [ -n "${p:-}" ] || return 1
  echo "$p"
}

# The recorded pid, only if it is still the same process instance we started.
read_pid() {
  local f; f=$(pidfile "$1")
  [ -f "$f" ] || return 1
  local p recorded current
  read -r p recorded <"$f" 2>/dev/null || return 1
  [ -n "${p:-}" ] || return 1
  current=$(starttime "$p") || return 1
  [ -n "$current" ] || return 1
  # no recorded starttime = pidfile from an older version; refuse to trust it
  [ -n "${recorded:-}" ] && [ "$current" = "$recorded" ] || return 1
  echo "$p"
}

# `kill -0 -1` is the POSIX broadcast to every process the user owns, not a
# lookup of group 1 — so it always succeeds and would make a pid-1 pidfile look
# like a live group. start_one never writes a pid that low, but the guard costs
# one line and the alternative is a `kill -TERM -1`.
group_alive() { [ "$1" -gt 1 ] 2>/dev/null && kill -0 -"$1" 2>/dev/null; }

# Pidfiles written before the reuse guard hold a bare pid with no start time.
# Adopt them once — if the pid is alive and its group still looks like ours —
# rather than reporting a running service as orphaned.
adopt_legacy() {
  local name=$1 f p rest
  f=$(pidfile "$name")
  [ -f "$f" ] || return 0
  read -r p rest <"$f" 2>/dev/null || return 0
  [ -n "${p:-}" ] && [ -z "${rest:-}" ] || return 0
  if kill -0 "$p" 2>/dev/null && group_is_ours "$p"; then
    printf '%s %s\n' "$p" "$(starttime "$p")" >"$f"
  fi
}

# Does this process group still look like one of ours? Used only when the
# leader has died and the children outlived it, where starttime can no longer
# vouch for identity — without this the orphan path could group-kill a
# recycled pid.
group_is_ours() {
  pgrep -g "$1" -f 'uvicorn|next-server|next dev|npm run dev' >/dev/null 2>&1
}

preflight() {
  [ -d /proc ] || die "needs /proc (Linux/WSL2). On macOS use: docker compose up --build"
  command -v setsid >/dev/null 2>&1 || die "needs setsid (util-linux). On macOS use: docker compose up --build"
  command -v ss >/dev/null 2>&1 || command -v lsof >/dev/null 2>&1 ||
    die "needs ss or lsof to check ports"
  [ -x backend/.venv/bin/uvicorn ] ||
    die "backend venv missing — cd backend && uv venv .venv && uv pip install -e '.[dev]' --python .venv/bin/python"
  [ -d frontend/node_modules ] || die "frontend deps missing — cd frontend && npm ci"
  for p in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    case $p in ''|*[!0-9]*) die "port must be numeric, got '$p'" ;; esac
  done
}

start_one() {
  local name=$1 port cmd
  port=$(port_of "$name")
  local existing
  if existing=$(read_pid "$name"); then
    printf '  %s already running (pid %s)\n' "$name" "$existing"
    return 0
  fi
  # a stale pidfile is fine, but something else on the port is not — starting
  # anyway would leave a service that looks up but is not ours
  if port_busy "$port"; then
    die "port $port is already in use by something else (wanted for $name)"
  fi
  rm -f "$(pidfile "$name")"
  case $name in
    backend)  cmd=(.venv/bin/uvicorn app.main:app --port "$port" --reload) ;;
    frontend) cmd=(npm run dev -- --port "$port") ;;
    *) die "unknown service '$name'" ;;
  esac
  # keep one previous session's log rather than appending forever
  mv -f "$(logfile "$name")" "$(logfile "$name").1" 2>/dev/null || true
  # setsid so the service leads its own process group: uvicorn --reload and
  # next dev both fork children, and killing the group is the only way to take
  # the whole tree down later. Args are passed positionally so a repo path
  # containing a quote or space cannot break out of the shell string.
  setsid bash -c 'cd "$1" && shift && exec "$@"' _ "$ROOT/$name" "${cmd[@]}" \
    >>"$(logfile "$name")" 2>&1 </dev/null &
  local pid=$!
  printf '%s %s\n' "$pid" "$(starttime "$pid")" >"$(pidfile "$name")"
  printf '  %s starting (pid %s, port %s)\n' "$name" "$pid" "$port"
}

stop_one() {
  local name=$1 pid="" mode=""
  if pid=$(read_pid "$name"); then
    mode=ours
  elif pid=$(raw_pid "$name") && group_alive "$pid" && group_is_ours "$pid"; then
    # leader died (OOM, stray kill) but its children still hold the port
    mode=orphaned
  else
    local stale; stale=$(raw_pid "$name" 2>/dev/null || true)
    rm -f "$(pidfile "$name")"
    if [ -n "$stale" ]; then
      printf '  %s not running (pid %s is not ours — stale pidfile cleared)\n' "$name" "$stale"
    else
      printf '  %s not running\n' "$name"
    fi
    return 0
  fi

  [ "$mode" = orphaned ] && printf '  %s leader gone, killing orphaned group %s\n' "$name" "$pid"
  kill -TERM -"$pid" 2>/dev/null || true
  # wait on the GROUP, not the leader: uvicorn's parent can exit on TERM while
  # a worker keeps the port, and watching only the leader would skip the KILL
  local waited=0
  while [ "$waited" -lt 40 ] && group_alive "$pid"; do
    sleep 0.25
    waited=$((waited + 1))
  done
  if group_alive "$pid"; then
    kill -KILL -"$pid" 2>/dev/null || true
    printf '  %s killed (group %s did not exit on TERM)\n' "$name" "$pid"
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
    for s in "${SERVICES[@]}"; do
      read_pid "$s" >/dev/null || {
        printf '\n'
        die "$s exited during startup (other services left running) — ./scripts/skein.sh logs $s"
      }
    done
    printf '.'
    sleep 1
    waited=$((waited + 1))
  done
  printf '\n'
  die "not healthy after ${waited}s (services left running) — ./scripts/skein.sh logs"
}

cmd_start() {
  preflight
  mkdir -p "$RUN"
  printf 'starting skein\n'
  for s in "${SERVICES[@]}"; do start_one "$s"; done
  wait_healthy
  cmd_status || true
}

cmd_stop() {
  printf 'stopping skein\n'
  for s in "${SERVICES[@]}"; do adopt_legacy "$s"; done
  for s in "${SERVICES[@]}"; do stop_one "$s"; done
}

# exit 0 only when every service is up, so `status && ...` means what it looks like
cmd_status() {
  local down=0 pid port
  for s in "${SERVICES[@]}"; do adopt_legacy "$s"; done
  for s in "${SERVICES[@]}"; do
    port=$(port_of "$s")
    if pid=$(read_pid "$s"); then
      printf '  %sup%s    %-9s pid %-8s http://localhost:%s\n' "$c_ok" "$c_off" "$s" "$pid" "$port"
    elif pid=$(raw_pid "$s" 2>/dev/null) && group_alive "$pid" && group_is_ours "$pid"; then
      down=1
      printf '  %s??%s    %-9s leader gone, orphaned group %s still up — run stop\n' \
        "$c_bad" "$c_off" "$s" "$pid"
    elif port_busy "$port"; then
      down=1
      printf '  %s??%s    %-9s port %s busy, but not ours\n' "$c_bad" "$c_off" "$s" "$port"
    else
      down=1
      printf '  %sdown%s  %-9s\n' "$c_bad" "$c_off" "$s"
    fi
  done
  local health parsed
  if health=$(curl -fsS --max-time 2 "http://localhost:$BACKEND_PORT/health" 2>/dev/null); then
    parsed=$(sed -n 's/.*"provider":"\([^"]*\)".*"model":"\([^"]*\)".*/provider \1 · model \2/p' <<<"$health")
    [ -n "$parsed" ] && printf '  %s%s%s\n' "$c_dim" "$parsed" "$c_off"
  fi
  return "$down"
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
  for s in "${SERVICES[@]}"; do
    port_busy "$(port_of "$s")" &&
      die "port $(port_of "$s") is in use (already started? ./scripts/skein.sh stop)"
  done
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
  *)       usage; exit 1 ;;
esac
