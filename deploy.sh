#!/bin/bash
# =============================================================================
# deploy.sh — 跨服务器一键部署
# 用法：bash deploy.sh [目标安装路径]
#
# 示例：
#   cd ~/cancer-demo && bash deploy.sh
#   bash deploy.sh ~/cancer-demo
#
# 假设：代码已经通过 git clone / tar 解压到目标机上。
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_PROJECT="${1:-$SCRIPT_DIR}"

# ---------- 颜色 ----------
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()  { echo -e "\n${GREEN}========== $* ==========${NC}"; }

# ---------- 前置检查 ----------
step "0/5  环境检查"

command -v conda >/dev/null 2>&1 || error "找不到 conda，请先安装 Miniconda/Anaconda 并 conda init"
info "conda: $(command -v conda)"

[ -f "$DEST_PROJECT/requirements.txt" ] || error "找不到 $DEST_PROJECT/requirements.txt，请确认在项目根目录执行"
[ -f "$DEST_PROJECT/caffe-py38-environment.yml" ] || warn "缺少 caffe-py38-environment.yml，将走手动安装"
info "项目根: $DEST_PROJECT"

# =============================================================================
# 1. 创建运行时目录
# =============================================================================
step "1/5  创建运行时目录"

mkdir -p "$DEST_PROJECT"/{upload,wsi-result,logs,pids,tmp}
mkdir -p "$DEST_PROJECT/wsi-result"/{brca-contour,brca-transparent,brca-aln}
info "目录创建完成"

# =============================================================================
# 2. 配置 .env
# =============================================================================
step "2/5  配置 .env"

if [ ! -f "$DEST_PROJECT/env.example" ]; then
    warn "缺少 env.example，跳过 .env 生成"
else
    for agent_dir in leader-agent \
                     partners/brca-pre-agent \
                     partners/coad-pre-agent \
                     partners/seg-agent \
                     partners/alnpre-agent; do
        env_file="$DEST_PROJECT/$agent_dir/server-api/.env"
        if [ -f "$env_file" ]; then
            info "$agent_dir/.env 已存在，跳过"
        else
            cp "$DEST_PROJECT/env.example" "$env_file"
            info "已创建 $agent_dir/.env"
        fi
    done
fi

echo ""
warn "==> 请务必编辑 leader-agent/server-api/.env，填写你的 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL"
warn "    nano $DEST_PROJECT/leader-agent/server-api/.env"
echo ""

# =============================================================================
# 3. caffe-py38 conda 环境
# =============================================================================
step "3/5  检查 / 安装 caffe-py38 环境"

if conda env list 2>/dev/null | awk '{print $1}' | grep -qx "caffe-py38"; then
    info "caffe-py38 已存在，跳过"
else
    CAFFE_YML="$DEST_PROJECT/caffe-py38-environment.yml"
    if [ -f "$CAFFE_YML" ]; then
        info "从 caffe-py38-environment.yml 恢复（10-20 分钟）..."
        conda env create -f "$CAFFE_YML" || {
            warn "yml 恢复失败，手动安装..."
            conda create -n caffe-py38 python=3.8 -y
            conda install -n caffe-py38 -c conda-forge \
                caffe=1.0 numpy=1.24 scipy=1.8 Pillow=8.4 -y
        }
    else
        warn "缺少 yml，手动安装..."
        conda create -n caffe-py38 python=3.8 -y
        conda install -n caffe-py38 -c conda-forge \
            caffe=1.0 numpy=1.24 scipy=1.8 Pillow=8.4 -y
    fi
fi

info "验证 Caffe..."
if PYTHONNOUSERSITE=1 conda run -n caffe-py38 python -s -c "import caffe" 2>/dev/null; then
    info "Caffe 验证通过 ✓"
else
    warn "Caffe 导入有警告（可能是 protobuf 提示），继续..."
fi

# =============================================================================
# 4. brcapre-dev 主环境
# =============================================================================
step "4/5  检查 / 安装 brcapre-dev 环境"

if conda env list 2>/dev/null | awk '{print $1}' | grep -qx "brcapre-dev"; then
    info "brcapre-dev 已存在"
else
    warn "创建 brcapre-dev (python 3.10)..."
    conda create -n brcapre-dev python=3.10 -y
    info "brcapre-dev 创建完成"
fi

info "安装 pip 依赖..."
conda run -n brcapre-dev pip install -r "$DEST_PROJECT/requirements.txt" -q
info "pip 依赖安装完成"

# =============================================================================
# 5/6  检查模型文件（全部需要自备）
# =============================================================================
step "5/6  检查模型文件"

# ---- 读取 .env 里的覆盖路径（如果用户设置过）----
get_env_var() {
    local env_file="$1" key="$2"
    [ -f "$env_file" ] || return 0
    grep -E "^${key}=" "$env_file" 2>/dev/null | tail -n1 | cut -d= -f2- \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

BRCA_ENV="$DEST_PROJECT/partners/brca-pre-agent/server-api/.env"
COAD_ENV="$DEST_PROJECT/partners/coad-pre-agent/server-api/.env"
SEG_ENV="$DEST_PROJECT/partners/seg-agent/server-api/.env"
ALNPRE_ENV="$DEST_PROJECT/partners/alnpre-agent/server-api/.env"

# ---- 乳腺：优先 env 覆盖，其次默认路径 ----
BRCA_GLOBAL="$(get_env_var "$BRCA_ENV" BRCA_GLOBAL_MODEL_PATH)"
[ -z "$BRCA_GLOBAL" ] && BRCA_GLOBAL="$HOME/BreastCancerCNN-master/model/3_global_googlenet_iter_7000_0.929_0.21.caffemodel"

BRCA_PATCH="$(get_env_var "$BRCA_ENV" BRCA_PATCH_MODEL_PATH)"
[ -z "$BRCA_PATCH" ] && BRCA_PATCH="$HOME/BreastCancerCNN-master/model-patch/3_patch_googlenet_iter_70000_0.859_0.314.caffemodel"

# ---- 结肠 / 分割：目录 ----
COAD_PATH="$(get_env_var "$COAD_ENV" COAD_CHECKPOINT_PATH)"
[ -z "$COAD_PATH" ] && COAD_PATH="$DEST_PROJECT/partners/coad-pre-agent/server-api/checkpoints"

SEG_PATH="$(get_env_var "$SEG_ENV" SEG_CHECKPOINT_PATH)"
[ -z "$SEG_PATH" ] && SEG_PATH="$DEST_PROJECT/partners/seg-agent/server-api/checkpoints"

# ---- 淋巴结：具体文件 ----
ALNPRE_CKPT="$(get_env_var "$ALNPRE_ENV" ALNPRE_CHECKPOINT_PATH)"
[ -z "$ALNPRE_CKPT" ] && ALNPRE_CKPT="$DEST_PROJECT/partners/alnpre-agent/server-api/checkpoints/2.pth"

# ---- 模型扩展名白名单 ----
MODEL_EXTS='-name "*.pth" -o -name "*.pt" -o -name "*.ckpt" -o -name "*.caffemodel" -o -name "*.h5" -o -name "*.onnx" -o -name "*.pb" -o -name "*.bin"'

MISSING_MODELS=()

# 检查具体文件（含扩展名白名单）
check_model_file() {
    local label="$1" path="$2"
    if [ -f "$path" ]; then
        info "✅ $label: $path"
        return 0
    fi
    warn "❌ $label 缺失: $path"
    MISSING_MODELS+=("$label|$path")
    return 1
}

# 检查目录（至少包含一个模型文件）
check_model_dir() {
    local label="$1" path="$2"
    if [ ! -d "$path" ]; then
        warn "❌ $label 缺失（目录不存在）: $path"
        MISSING_MODELS+=("$label|$path")
        return 1
    fi
    local found
    found="$(eval find "\"$path\"" -maxdepth 2 -type f \\( $MODEL_EXTS \\) 2>/dev/null | head -n1)"
    if [ -n "$found" ]; then
        info "✅ $label: $path （含 $(basename "$found")）"
        return 0
    fi
    warn "❌ $label 目录内无模型文件: $path"
    MISSING_MODELS+=("$label|$path")
    return 1
}

# 出于隐私和版权考虑，所有模型文件均不随代码分发
check_model_file "乳腺-全局模型"  "$BRCA_GLOBAL"
check_model_file "乳腺-patch模型" "$BRCA_PATCH"
check_model_dir  "结肠-分类模型"  "$COAD_PATH"
check_model_dir  "分割-模型"      "$SEG_PATH"
check_model_file "淋巴结-模型"    "$ALNPRE_CKPT"

if [ "${#MISSING_MODELS[@]}" -eq 0 ]; then
    info "所有模型文件已就位 ✓"
else
    warn "有 ${#MISSING_MODELS[@]} 个模型缺失，请在启动前补齐（详见 README §4）"
fi

# =============================================================================
# 6/6  自动填充 LEADER_PUBLIC_BASE_URL 并输出后续步骤
# =============================================================================
step "6/6  配置 LEADER_PUBLIC_BASE_URL"

LEADER_ENV="$DEST_PROJECT/leader-agent/server-api/.env"
LOCAL_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP="127.0.0.1"
    warn "无法自动获取本机 IP，回退到 $LOCAL_IP"
fi
PUBLIC_URL="http://${LOCAL_IP}:8021"

if [ -f "$LEADER_ENV" ]; then
    if grep -q "^LEADER_PUBLIC_BASE_URL=" "$LEADER_ENV"; then
        sed -i.bak "s|^LEADER_PUBLIC_BASE_URL=.*|LEADER_PUBLIC_BASE_URL=${PUBLIC_URL}|" "$LEADER_ENV"
        rm -f "${LEADER_ENV}.bak"
    else
        echo "" >> "$LEADER_ENV"
        echo "LEADER_PUBLIC_BASE_URL=${PUBLIC_URL}" >> "$LEADER_ENV"
    fi
    info "已写入 LEADER_PUBLIC_BASE_URL=$PUBLIC_URL"
else
    warn "未找到 $LEADER_ENV，请手动设置 LEADER_PUBLIC_BASE_URL=$PUBLIC_URL"
fi

# =============================================================================
# 部署完成
# =============================================================================
step "部署完成"

cat <<EOF

============================ 下一步 ============================

[1] 填写大模型 API Key（必填）
    nano $LEADER_ENV
    设置：
      OPENAI_API_KEY=sk-xxxxxxxx
      OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
      OPENAI_MODEL=qwen-plus

[2] 准备模型文件（出于隐私和版权考虑，所有模型均不随代码分发）
EOF

if [ "${#MISSING_MODELS[@]}" -eq 0 ]; then
    echo "    ✅ 所有模型已就位，无需操作"
else
    echo "    ❌ 缺失以下 ${#MISSING_MODELS[@]} 个模型，请向原作者/数据方获取："
    for entry in "${MISSING_MODELS[@]}"; do
        label="${entry%%|*}"
        path="${entry#*|}"
        echo "       - $label"
        echo "         放置到: $path"
    done
    echo ""
    echo "    若模型放在其他路径，在对应 agent 的 .env 中覆盖："
    echo "      partners/brca-pre-agent/server-api/.env"
    echo "        BRCA_GLOBAL_MODEL_PATH=/your/path/global.caffemodel"
    echo "        BRCA_PATCH_MODEL_PATH=/your/path/patch.caffemodel"
    echo "      partners/coad-pre-agent/server-api/.env"
    echo "        COAD_CHECKPOINT_PATH=/your/path/checkpoints"
    echo "      partners/seg-agent/server-api/.env"
    echo "        SEG_CHECKPOINT_PATH=/your/path/checkpoints"
    echo "      partners/alnpre-agent/server-api/.env"
    echo "        ALNPRE_CHECKPOINT_PATH=/your/path/2.pth"
    echo ""
    echo "    设置好路径后重新运行 deploy.sh 可再次检查"
fi

cat <<EOF

[3] 启动系统
    conda activate brcapre-dev
    cd $DEST_PROJECT
    bash scripts/start_system.sh

[4] 浏览器访问
    $PUBLIC_URL/

[5] 健康检查
    curl $PUBLIC_URL/health

[6] 停止系统
    bash scripts/stop_system.sh

[7] 路径自检（排查图片 404）
    bash scripts/check_paths.sh

===============================================================
EOF

info "部署完成，请按上述步骤继续"