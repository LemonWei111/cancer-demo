#!/usr/bin/env bash
# /home/user/weijing/cancer-demo-new/scripts/start_system.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
PID_DIR="$PROJECT_ROOT/pids"
COMMON_DIR="$PROJECT_ROOT/common"

mkdir -p "$LOG_DIR" "$PID_DIR"

# conda
CONDA_SH=""
for path in "${HOME}/anaconda3/etc/profile.d/conda.sh" \
            "${HOME}/miniconda3/etc/profile.d/conda.sh"; do
    [ -f "$path" ] && CONDA_SH="$path" && break
done
if [ -z "$CONDA_SH" ]; then
    echo "❌ 找不到 conda.sh" >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate brcapre-dev
echo "✅ conda 环境: brcapre-dev"

start_agent() {
    local name="$1" workdir="$2" extra_env="${3:-}"
    local work_path="$PROJECT_ROOT/$workdir"
    if [ ! -d "$work_path" ]; then
        echo "⚠️  跳过 ${name}: $work_path 不存在"
        return 0
    fi
    echo "==> 启动 ${name}"
    (
        cd "$work_path"
        if [ -n "$extra_env" ]; then
            env PYTHONPATH="$COMMON_DIR:${PYTHONPATH:-}" $extra_env \
                nohup python main.py >> "$LOG_DIR/${name}.log" 2>&1 &
        else
            env PYTHONPATH="$COMMON_DIR:${PYTHONPATH:-}" \
                nohup python main.py >> "$LOG_DIR/${name}.log" 2>&1 &
        fi
        echo $! > "$PID_DIR/${name}.pid"
    )
    sleep 0.5
}

start_agent "seg-agent"       "partners/seg-agent/server-api"       "CUDA_VISIBLE_DEVICES=3"
start_agent "brca-pre-agent"  "partners/brca-pre-agent/server-api"
start_agent "coad-pre-agent"  "partners/coad-pre-agent/server-api"
start_agent "alnpre-agent"    "partners/alnpre-agent/server-api"
start_agent "leader-agent"    "leader-agent/server-api"

FRONTEND_DIR="$PROJECT_ROOT/leader-agent/client-web"
if [ -d "$FRONTEND_DIR" ]; then
    echo "==> 启动前端静态页面 (8080)"
    (cd "$FRONTEND_DIR"
     nohup python3 -m http.server 8080 >> "$LOG_DIR/frontend.log" 2>&1 &
     echo $! > "$PID_DIR/frontend.pid")
fi

echo
echo "主入口: http://<IP>:8021/"
echo "备用:   http://<IP>:8080/"
echo "健康:   http://<IP>:8021/health"