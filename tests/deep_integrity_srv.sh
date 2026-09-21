#!/usr/bin/env bash
# Q05 专属实例（18932）受控重启：只按 PID+cwd 确认属于本任务进程再操作。
set -euo pipefail
ROOT="/Users/gukepeng/Desktop/ZHDL/code/wiz_ai/wiz_kq_builder_v2/worktree/test"
EVID="$ROOT/.runtime/test-evidence/q05"
PIDFILE="$EVID/server-18932.pid"
LOGF="$ROOT/.runtime/test-evidence/server-18932.log"

listen_pid() { lsof -ti tcp:18932 -sTCP:LISTEN 2>/dev/null | head -1 || true; }

pid_cwd() { lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; }

cmd="$1"
case "$cmd" in
  stop)
    PID="$(listen_pid)"
    [ -n "$PID" ] || { echo "no listener on 18932"; exit 0; }
    CWD="$(pid_cwd "$PID")"
    if [ "$CWD" != "$ROOT" ]; then echo "REFUSE: pid $PID cwd=$CWD 不是本任务目录"; exit 1; fi
    kill "$PID"; sleep 1.5
    if kill -0 "$PID" 2>/dev/null; then kill -9 "$PID"; sleep 0.5; fi
    echo "stopped $PID"
    ;;
  kill9)
    PID="$(listen_pid)"
    [ -n "$PID" ] || { echo "no listener"; exit 1; }
    CWD="$(pid_cwd "$PID")"
    if [ "$CWD" != "$ROOT" ]; then echo "REFUSE: pid $PID cwd=$CWD"; exit 1; fi
    kill -9 "$PID"
    echo "killed-9 $PID"
    ;;
  start)
    if [ -n "$(listen_pid)" ]; then echo "already listening"; exit 0; fi
    cd "$ROOT"
    WIZ_WORKBENCH_ROOT="$ROOT/.runtime/test-data-q05" WIZ_WORKBENCH_PORT=18932 \
      nohup .runtime/venv/bin/python -m workbench.server >> "$LOGF" 2>&1 &
    NEW=$!
    echo "$NEW" > "$PIDFILE"
    for i in $(seq 1 60); do
      code=$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:18932/api/auth-state || true)
      [ "$code" = "200" ] && { echo "started pid=$NEW ready"; exit 0; }
      sleep 0.5
    done
    echo "FAILED to become ready; see $LOGF"; exit 1
    ;;
  status)
    echo "listener=$(listen_pid) cwd=$(pid_cwd "$(listen_pid || echo 0)")"
    ;;
  *) echo "usage: $0 stop|kill9|start|status"; exit 2;;
esac
