#!/usr/bin/env bash
# 本体工作台服务管理（端口 18765）
# 用法: ./start.sh {setup|start|stop|restart|status|rebuild|help}
# 必须明确指定操作；不传参数仅显示帮助，不启动服务。
#
# 端口约定（重要）：
#   本工作台固定使用 18765；8765 保留给机器上的其他服务（如 graph_recall_test_server），
#   本脚本绝不绑定、检查或杀灭 8765 上的任何进程。WIZ_WORKBENCH_PORT 可覆盖端口，
#   但显式设为 8765 会被直接拒绝。
#
#   setup    显式安装依赖（后端 pip + 前端 npm install），成功后写依赖戳记
#   start    启动（已在运行则提示）；启动时把 PID 写入 .runtime/server.pid
#   stop     停止：只 kill 本脚本记录的 PID（ps 校验命令与工作目录，防 PID 复用误杀）；
#            不做任何“按端口找进程杀”的操作；18765 上的陌生进程只报告不处理
#   restart  重启（前端产物缺失时才构建）
#   status   按 pid 文件 + ps 校验报告运行状态
#   rebuild  先在临时目录完成前端构建，成功后才替换 dist 并切换服务；
#            构建失败时运行中的服务与现有 dist 均不动，报错退出。
#            依赖安装仅在 node_modules 缺失或 package-lock.json 哈希与戳记不一致时自动执行一次，
#            其余情况跳过（首次或依赖变化时的显式安装用 setup）。
#
# 环境变量透传给 python3 -m workbench.server：
#   WIZ_WORKBENCH_PORT  覆盖端口（默认 18765；不允许 8765）
#   WIZ_WORKBENCH_ROOT  挂载独立数据目录（仅自动化测试用）
# 运行产物（本脚本自管）：.runtime/server.pid、.runtime/deps.stamp、.runtime/server.log、.runtime/build-tmp
set -uo pipefail

usage() {
  cat <<'USAGE'
用法: ./start.sh <参数>（服务端口默认 18765）

可用参数：
  setup    安装依赖并更新依赖戳记
  start    启动服务（已运行时仅提示）
  stop     停止服务
  restart  重启服务（前端产物缺失时才构建）
  status   查看运行状态
  rebuild  重新构建前端，成功后重启服务
  help     显示帮助（也支持 -h、--help）

示例: ./start.sh start
不传参数只显示此帮助，不执行启停或安装操作。
USAGE
}

# 在目录切换、端口检查和任何服务操作之前校验参数。
if [ "$#" -eq 0 ]; then
  usage
  exit 0
fi
if [ "$#" -ne 1 ]; then
  echo "✗ 请只传入一个操作参数" >&2
  usage >&2
  exit 2
fi
case "$1" in
  help|-h|--help) usage; exit 0 ;;
  setup|start|stop|restart|status|rebuild) ;;
  *) echo "✗ 未知参数: $1" >&2; usage >&2; exit 2 ;;
esac

cd "$(dirname "$0")"
ROOT="$(pwd)"

PORT="${WIZ_WORKBENCH_PORT:-18765}"
if [ "$PORT" = "8765" ]; then
  echo "✗ 拒绝使用端口 8765：该端口保留给其他服务，本工作台固定 18765"
  exit 1
fi
# 关键：把端口作为环境变量传给服务进程，脚本内检查与实际监听端口永远一致
export WIZ_WORKBENCH_PORT="$PORT"

LOG=.runtime/server.log
PID_FILE=.runtime/server.pid
STAMP_FILE=.runtime/deps.stamp
BUILD_TMP=.runtime/build-tmp

# ---------- 进程识别：只认 pid 文件记录的进程，绝不用“端口上的所有 PID”作为可杀集合 ----------

# 判断 PID 是否为本工作台服务：命令行含 workbench.server 且工作目录是本仓库根。
# 两个条件都满足才认领，防止 PID 被回收复用后误杀无关进程。
is_ours() {
  local pid="$1" cmd cwd
  cmd="$(ps -ww -p "$pid" -o command= 2>/dev/null)" || return 1
  [ -n "$cmd" ] || return 1
  case "$cmd" in
    *workbench.server*) ;;
    *) return 1 ;;
  esac
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  [ "$cwd" = "$ROOT" ]
}

recorded_pid() { cat "$PID_FILE" 2>/dev/null | tr -d '[:space:]'; }

# 端口 18765 上是否存在“本工作台”进程（无 pid 文件的实例，如旧脚本遗留）
find_ours_on_port() {
  local p
  for p in $(lsof -ti :"$PORT" 2>/dev/null); do
    if is_ours "$p"; then echo "$p"; return 0; fi
  done
  return 1
}

# 端口 18765 上的非本工作台进程（只报告，绝不自动 kill）
foreign_on_port() {
  local p cmd out=""
  for p in $(lsof -ti :"$PORT" 2>/dev/null); do
    if ! is_ours "$p"; then
      cmd="$(ps -ww -p "$p" -o command= 2>/dev/null | cut -c1-60)"
      out="$out ${p}(${cmd})"
    fi
  done
  [ -n "$out" ] && echo "$out"
  return 0
}

kill_and_wait() {
  local pid="$1"
  kill "$pid" 2>/dev/null
  local _; for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.3; done
  kill -9 "$pid" 2>/dev/null
  local __; for __ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.2; done
  return 1
}

# ---------- 依赖：显式 setup + 戳记，锁文件未变不重复 install ----------

lock_hash() { shasum -a 256 frontend/package-lock.json 2>/dev/null | awk '{print $1}'; }

ensure_python_deps() {
  command -v python3 >/dev/null || { echo "✗ 未找到 python3，请先安装"; exit 1; }
  if ! python3 -c "import yaml, rdflib" >/dev/null 2>&1; then
    echo "首次运行：安装后端依赖（PyYAML / rdflib）…"
    python3 -m pip install -r requirements.txt || { echo "✗ 依赖安装失败"; exit 1; }
  fi
}

npm_install() {
  command -v npm >/dev/null || { echo "✗ 未找到 npm，请先安装 Node.js"; exit 1; }
  (cd frontend && npm install) || { echo "✗ npm install 失败"; exit 1; }
  mkdir -p .runtime
  lock_hash > "$STAMP_FILE"
}

# rebuild 前的条件依赖安装：node_modules 缺失或锁文件哈希与戳记不一致时自动装一次，否则跳过
ensure_node_deps() {
  if [ -d frontend/node_modules ] && [ -f "$STAMP_FILE" ]; then
    if [ "$(lock_hash)" = "$(cat "$STAMP_FILE" 2>/dev/null | tr -d '[:space:]')" ]; then
      echo "依赖未变化，跳过 npm install（显式安装/刷新戳记请用 ./start.sh setup）"
      return 0
    fi
    echo "package-lock.json 哈希与戳记不一致（依赖已变化）：自动执行一次 npm install（显式管理请用 ./start.sh setup）"
  elif [ -d frontend/node_modules ]; then
    echo "未找到依赖戳记（首次使用新版脚本或依赖变化）：自动执行一次 npm install（显式管理请用 ./start.sh setup）"
  else
    echo "node_modules 缺失：自动执行一次 npm install（显式管理请用 ./start.sh setup）"
  fi
  npm_install
}

# ---------- 前端构建：临时目录构建，成功后原子切换，失败不动运行中的服务 ----------

build_frontend() {
  echo "构建前端（先输出到 ${BUILD_TMP}，成功后才替换 frontend/dist）…"
  command -v npm >/dev/null || { echo "✗ 未找到 npm，请先安装 Node.js"; exit 1; }
  rm -rf "$BUILD_TMP"
  if ! (cd frontend && npm run build -- --outDir "$ROOT/$BUILD_TMP" --emptyOutDir); then
    rm -rf "$BUILD_TMP"
    return 1
  fi
  if [ ! -f "$BUILD_TMP/index.html" ]; then
    echo "✗ 构建产物缺少 index.html"
    rm -rf "$BUILD_TMP"
    return 1
  fi
  rm -rf frontend/dist.old
  [ -d frontend/dist ] && mv frontend/dist frontend/dist.old
  if ! mv "$BUILD_TMP" frontend/dist; then
    echo "✗ 切换构建产物失败，尝试回滚"
    [ -d frontend/dist.old ] && mv frontend/dist.old frontend/dist
    rm -rf "$BUILD_TMP"
    return 1
  fi
  rm -rf frontend/dist.old
  return 0
}

# ---------- 服务启停 ----------

wait_ready() {
  local _
  for _ in $(seq 1 30); do
    curl -sf "http://127.0.0.1:$PORT/api/ontologies" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}

start_service() {
  local foreign pid
  if foreign="$(foreign_on_port)" && [ -n "$foreign" ]; then
    echo "✗ 端口 $PORT 被非本工作台进程占用:$foreign"
    echo "  为避免误杀，本脚本不自动处理；请确认后手动处理该进程"
    exit 1
  fi
  mkdir -p .runtime
  nohup python3 -m workbench.server > "$LOG" 2>&1 &
  pid=$!
  echo "$pid" > "$PID_FILE"
  if wait_ready; then
    echo "✔ 本体工作台已启动: http://127.0.0.1:$PORT （PID $pid → ${PID_FILE}）"
    echo "  日志: $LOG"
    open "http://127.0.0.1:$PORT" 2>/dev/null || true
  else
    echo "✗ 启动失败，最近日志："
    tail -10 "$LOG"
    kill "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    exit 1
  fi
}

stop_service() {
  local pid legacy foreign
  pid="$(recorded_pid)"
  if [ -n "$pid" ] && is_ours "$pid"; then
    if kill_and_wait "$pid"; then
      echo "✔ 已停止（PID ${pid}）"
    else
      echo "✗ PID $pid 未能停止，请手动检查"; exit 1
    fi
    rm -f "$PID_FILE"
    return 0
  fi
  if [ -n "$pid" ]; then
    echo "pid 文件（$PID_FILE → ${pid}）指向的进程已退出或被其他程序复用，忽略并清理"
    rm -f "$PID_FILE"
  fi
  legacy="$(find_ours_on_port)" || true
  if [ -n "${legacy:-}" ]; then
    echo "发现无 pid 文件的本工作台进程（PID ${legacy}，按命令与工作目录识别），停止"
    if kill_and_wait "$legacy"; then
      echo "✔ 已停止（PID ${legacy}）"
    else
      echo "✗ PID $legacy 未能停止，请手动检查"; exit 1
    fi
    return 0
  fi
  foreign="$(foreign_on_port)"
  if [ -n "$foreign" ]; then
    echo "端口 $PORT 上是非本工作台进程:$foreign —— 不自动处理"
  else
    echo "服务未在运行"
  fi
}

# ---------- 命令分发 ----------

case "$1" in
  setup)
    ensure_python_deps
    npm_install
    echo "✔ 依赖安装完成；戳记 ${STAMP_FILE}（package-lock.json sha256: $(cat "$STAMP_FILE")）"
    ;;
  start)
    pid="$(recorded_pid)"
    if [ -n "$pid" ] && ! is_ours "$pid"; then
      echo "pid 文件（$PID_FILE → ${pid}）指向的进程已退出或被其他程序复用，忽略并清理"
      rm -f "$PID_FILE"
      pid=""
    fi
    if [ -n "$pid" ]; then
      echo "服务已在运行: PID $pid → http://127.0.0.1:$PORT （重启请用 ./start.sh restart）"
    else
      legacy="$(find_ours_on_port)" || true
      if [ -n "${legacy:-}" ]; then
        echo "$legacy" > "$PID_FILE"
        echo "服务已在运行（未纳管实例，已纳入管理）: PID $legacy → http://127.0.0.1:$PORT"
      else
        ensure_python_deps
        if [ ! -f frontend/dist/index.html ]; then
          ensure_node_deps
          build_frontend || { echo "✗ 前端构建失败"; exit 1; }
        fi
        start_service
      fi
    fi
    ;;
  stop)
    stop_service
    ;;
  restart)
    stop_service
    ensure_python_deps
    if [ ! -f frontend/dist/index.html ]; then
      ensure_node_deps
      build_frontend || { echo "✗ 前端构建失败"; exit 1; }
    fi
    start_service
    ;;
  status)
    pid="$(recorded_pid)"
    if [ -n "$pid" ] && is_ours "$pid"; then
      echo "✔ 运行中: http://127.0.0.1:$PORT （PID ${pid}）"
      curl -sf "http://127.0.0.1:$PORT/api/ontologies" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  本体:', '、'.join(i['name'] for i in d['items']))" 2>/dev/null
    else
      [ -n "$pid" ] && echo "pid 文件（$PID_FILE → ${pid}）指向的进程已退出或被其他程序复用"
      legacy="$(find_ours_on_port)" || true
      if [ -n "${legacy:-}" ]; then
        echo "✔ 运行中（未纳管）: PID $legacy → http://127.0.0.1:$PORT"
        echo "  可用 ./start.sh stop && ./start.sh start 纳入管理"
      else
        foreign="$(foreign_on_port)"
        if [ -n "$foreign" ]; then
          echo "✗ 未运行（端口 $PORT 被非本工作台进程占用:${foreign}）"
        else
          echo "✗ 未运行"
        fi
      fi
    fi
    ;;
  rebuild)
    ensure_python_deps
    ensure_node_deps
    if ! build_frontend; then
      echo "✗ 前端构建失败：运行中的服务与现有 frontend/dist 均未改动"
      exit 1
    fi
    echo "✔ 前端构建完成，切换服务…"
    stop_service
    start_service
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
