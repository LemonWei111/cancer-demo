#!/usr/bin/env bash
# /home/user/weijing/cancer-demo-new/scripts/stop_system.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"

stop_agent() {
    local name="$1"
    local pid_file="$PID_DIR/${name}.pid"
    [ -f "$pid_file" ] || return 0
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && ps -p "$pid" >/dev/null 2>&1; then
        echo "==> 停止 ${name} (PID $pid)"
        kill "$pid" 2>/dev/null || true
        sleep 0.3
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
}

kill_port() {
    local port="$1"
    local pids
    if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
    else
        pids="$(ss -lntp 2>/dev/null \
            | awk -v p=":$port" '$4 ~ p {print}' \
            | grep -oP 'pid=\K[0-9]+' | sort -u || true)"
    fi
    [ -z "$pids" ] && return 0
    for pid in $pids; do
        echo "==> 释放端口 ${port} (PID $pid)"
        kill "$pid" 2>/dev/null || true
        sleep 0.2
        kill -9 "$pid" 2>/dev/null || true
    done
}

for name in leader-agent seg-agent brca-pre-agent coad-pre-agent alnpre-agent frontend; do
    stop_agent "$name"
done

for port in 8011 8012 8013 8014 8021 8080; do
    kill_port "$port"
done

echo "==> 清理残留 ..."
for _ in 1 2 3 4 5; do
    PIDS="$(pgrep -f "$PROJECT_ROOT.*main.py" || true)"
    [ -z "$PIDS" ] && echo "✅ 全部退出" && exit 0
    echo "残留: $PIDS"
    echo "$PIDS" | xargs -r kill -9
    sleep 1
done