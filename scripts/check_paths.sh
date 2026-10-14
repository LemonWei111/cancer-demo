#!/usr/bin/env bash
# /home/user/weijing/cancer-demo-new/scripts/check_paths.sh
#
# 用途：排查"写盘路径"和"读盘路径"不一致导致的 404
# 用法：bash scripts/check_paths.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT/wsi-result"

echo "============================================================"
echo "期望唯一结果目录: $TARGET"
echo "============================================================"
echo

# ---------- 1. 搜索所有产出文件 ----------
echo "==> 搜索产出文件（contour / transparent / prediction csv）..."
found_any=0
misplaced=0

while IFS= read -r f; do
    found_any=1
    if [[ "$f" == "$TARGET"/* ]]; then
        echo "  ✅ $f"
    else
        echo "  ❌ $f"
        echo "     （不在 $TARGET 下）"
        misplaced=$((misplaced + 1))
    fi
done < <(find "$ROOT" \
    \( -name "*_cancer_contour.png" \
       -o -name "*_cancer_transparent.png" \
       -o -name "*_prediction.csv" \
       -o -name "*_batch_predictions.csv" \) \
    -not -path "*/node_modules/*" \
    2>/dev/null | sort)

if [ "$found_any" -eq 0 ]; then
    echo "  （没有找到任何产出文件——是不是还没跑过任务？）"
fi
echo

# ---------- 2. 检查历史遗留目录 ----------
echo "==> 检查历史遗留目录..."
legacy_dirs=(
    "$ROOT/partners/wsi-result"
    "$ROOT/wsi-result/../wsi-result"     # 有些代码写到 peers 目录
)
has_legacy=0
for d in "${legacy_dirs[@]}"; do
    # 跳过和 TARGET 相同的路径
    [[ "$d" == "$TARGET" ]] && continue
    if [ -d "$d" ]; then
        has_legacy=1
        echo "  ⚠️  存在: $d"
        ls -la "$d" | head -20
    fi
done
if [ "$has_legacy" -eq 0 ]; then
    echo "  ✅ 无遗留目录"
fi
echo

# ---------- 3. 检查静态服务目录是否可访问 ----------
echo "==> 检查 Leader 静态服务（本地）..."
if command -v curl >/dev/null 2>&1; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://127.0.0.1:8021/wsi-result/" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "403" ] || [ "$HTTP_CODE" = "404" ] || [ "$HTTP_CODE" = "200" ]; then
        echo "  ✅ Leader 在跑（/wsi-result/ 返回 $HTTP_CODE）"
    elif [ "$HTTP_CODE" = "000" ]; then
        echo "  ⚠️  Leader 未启动或端口不通"
    else
        echo "  ?  /wsi-result/ 返回 $HTTP_CODE"
    fi
else
    echo "  （跳过：未安装 curl）"
fi
echo

# ---------- 4. 汇总 ----------
echo "============================================================"
if [ "$misplaced" -eq 0 ] && [ "$has_legacy" -eq 0 ]; then
    echo "✅ 路径检查通过"
    exit 0
else
    echo "❌ 发现问题："
    [ "$misplaced" -gt 0 ] && echo "   - $misplaced 个文件不在 $TARGET 下"
    [ "$has_legacy" -eq 1 ] && echo "   - 存在历史遗留目录"
    echo
    echo "排查建议："
    echo "   1. 检查日志里各 agent 的 _PROJECT_ROOT 是否一致："
    echo "      grep '_PROJECT_ROOT' $ROOT/logs/*.log"
    echo "   2. 检查业务代码里的 parents[N] 层级是否正确"
    echo "   3. 确认没有代码再写死 'partners/wsi-result'"
    exit 1
fi