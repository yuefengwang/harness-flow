#!/usr/bin/env bash
# sw — Simple Workflow CLI (统一入口)
# 用法: /sw init --type=feature --name=task-id --session=ses_xxx
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASKS="$ROOT/workflow/tasks"; TPLS="$ROOT/workflow/templates"; STATUS="$ROOT/workflow/STATUS.md"
TRASH="${TASKS}/.trash"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'; BLUE='\033[0;34m'
ok()   { printf "${GREEN}[✓]${NC} %s\n" "$*"; }
err()  { printf "${RED}[✗]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
hdr()  { printf "\n${BLUE}━━━ %s ━━━${NC}\n" "$*"; }

STAGES=("01-brainstorming" "02-planning" "03-coding" "04-review" "05-archive")
STAGE_NAMES=("头脑风暴" "规划" "编码" "评审" "归档")

state_f() { echo "${TASKS}/$1/.state"; }
now() { date '+%Y-%m-%dT%H:%M:%S'; }
today() { date '+%Y-%m-%d'; }

usage() {
  echo "sw — Simple Workflow CLI (所有任务统一入口)"
  echo ""
  echo "  sw init    --type=feature --name=<id> [--session=<sid>]"
  echo "  sw status  --name=<id>"
  echo "  sw advance --name=<id>"
  echo "  sw resume  --name=<id>"
  echo "  sw list    [--trash]"
  echo "  sw remove  --name=<id>"
  echo "  sw restore --name=<id>"
  exit 1
}

[[ $# -eq 0 ]] && usage

CMD="$1"; shift

case "$CMD" in
  init)
    TYPE="feature"; NAME=""; SESSION=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --type=*) TYPE="${1#*=}" ;;
        --name=*) NAME="${1#*=}" ;;
        --session=*) SESSION="${1#*=}" ;;
        *) err "未知参数: $1" ;;
      esac
      shift
    done
    [[ -z "$NAME" ]] && { err "缺少 --name"; exit 1; }
    D="${TASKS}/${NAME}"
    if [[ -d "$D" ]]; then
      err "任务已存在: ${NAME}，使用 /sw resume --name=${NAME} 恢复"
      exit 1
    fi
    mkdir -p "$D"
    cp "${TPLS}"/*.md "$D/"
    cat > "$(state_f "$NAME")" << STATE
id: ${NAME}
type: ${TYPE}
session: ${SESSION:-N/A}
stage: "01-brainstorming"
stage_idx: 0
created_at: $(now)
updated_at: $(now)
STATE
    # 更新 STATUS.md
    python3 -c "
f=open('${STATUS}');c=f.read();f.close()
c=c.replace('活动任务: 无','活动任务: ${NAME}')
c=c.replace('当前阶段: N/A','当前阶段: 01-头脑风暴')
open('${STATUS}','w').write(c)" 2>/dev/null || true
    hdr "任务已创建: ${NAME}"
    ok "Stage: 01-头脑风暴"
    echo "  下一步: hooks/01-brainstorming.md → templates/01-brainstorming.md"
    echo "  完成后: /sw advance --name=${NAME}"
    ;;

  status)
    N=""; [[ $# -gt 0 ]] && N="$1"
    [[ -z "$N" ]] && { N=$(grep '活动任务:' "$STATUS" 2>/dev/null | awk -F': ' '{print $2}'); }
    [[ -z "$N" || "$N" == "无" ]] && { err "无活跃任务"; exit 1; }
    S=$(grep '^stage:' "$(state_f "$N")" 2>/dev/null | awk '{print $2}' | tr -d '"')
    echo "任务: $N | Stage: $S | $(grep '^updated_at:' "$(state_f "$N")" | awk -F': ' '{print $2}')"
    ;;

  advance)
    N=""
    # 解析 --name 参数
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --name=*) N="${1#*=}" ;;
        --name) N="$2"; shift ;;
        *) N="$1" ;;
      esac
      shift
    done
    [[ -f "${TASKS}/${N}/.state" ]] || N=$(grep '活动任务:' "$STATUS" 2>/dev/null | awk -F': ' '{print $2}')
    [[ -z "$N" ]] && { err "无活跃任务，请指定: /sw advance <task-name>"; exit 1; }
    SF=$(state_f "$N")
    S=$(grep '^stage:' "$SF" 2>/dev/null | awk -F'"' '{print $2}')
    IDX=$(grep '^stage_idx:' "$SF" 2>/dev/null | awk '{print $2}')
    if [[ "$IDX" -ge 4 ]]; then
      ok "任务已完成 (05-归档)"
      exit 0
    fi
    NI=$((IDX + 1))
    NS="${STAGES[$NI]}"
    NL="${STAGE_NAMES[$NI]}"
    sed -i '' "s/^stage:.*/stage: \"${NS}\"/" "$SF"
    sed -i '' "s/^stage_idx:.*/stage_idx: ${NI}/" "$SF"
    sed -i '' "s/^updated_at:.*/updated_at: $(now)/" "$SF"
    python3 -c "
f=open('${STATUS}');c=f.read();f.close()
c=c.replace('当前阶段: 01-头脑风暴','当前阶段: 0$((NI+1))-${NL}')
c=c.replace('当前阶段: 02-规划','当前阶段: 0$((NI+1))-${NL}')
c=c.replace('当前阶段: 03-编码','当前阶段: 0$((NI+1))-${NL}')
c=c.replace('当前阶段: 04-评审','当前阶段: 0$((NI+1))-${NL}')
open('${STATUS}','w').write(c)" 2>/dev/null || true
    hdr "阶段推进 → ${NL}"
    echo "  下一步: hooks/${NS}.md → templates/${NS}.md"
    echo "  完成后: /sw advance --name=${N}"
    ;;

  resume)
    N=""; [[ $# -gt 0 ]] && N="$1"
    [[ -z "$N" ]] && { err "请指定任务名: /sw resume --name=<id>"; exit 1; }
    SF=$(state_f "$N")
    [[ ! -f "$SF" ]] && { err "任务不存在: ${N}"; exit 1; }
    S=$(grep '^stage:' "$SF" | awk -F'"' '{print $2}')
    hdr "恢复任务: ${N}"
    echo "  Stage: ${S}"
    echo "  状态文件: ${SF}"
    echo "  继续: hooks/${S}.md → templates/${S}.md"
    echo "  完成后: /sw advance --name=${N}"
    ;;

  remove)
    N=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --name=*) N="${1#*=}" ;;
        --name) N="$2"; shift ;;
        *) N="$1" ;;
      esac
      shift
    done
    [[ -z "$N" ]] && { err "缺少 --name"; exit 1; }
    D="${TASKS}/${N}"
    if [[ ! -d "$D" ]]; then
      if [[ -d "${TRASH}/${N}" ]]; then
        err "任务已在回收站: ${N}"
        exit 1
      fi
      err "任务不存在: ${N}"
      exit 1
    fi
    mkdir -p "$TRASH"
    mv "$D" "${TRASH}/${N}"
    # 记录移除时间
    echo "removed_at: $(now)" >> "${TRASH}/${N}/.state"
    # 如果是当前活跃任务，清除 STATUS.md 中的引用
    ACTIVE=$(grep '活动任务:' "$STATUS" 2>/dev/null | awk -F': ' '{print $2}' | tr -d ' ')
    if [[ "$ACTIVE" == "$N" ]]; then
      sed -i '' 's/^活动任务:.*/活动任务: 无/' "$STATUS" 2>/dev/null || true
      sed -i '' 's/^当前阶段:.*/当前阶段: N\/A/' "$STATUS" 2>/dev/null || true
    fi
    ok "任务已移入回收站: ${N}"
    warn "恢复: sw restore --name=${N}"
    ;;

  restore)
    N=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --name=*) N="${1#*=}" ;;
        --name) N="$2"; shift ;;
        *) N="$1" ;;
      esac
      shift
    done
    [[ -z "$N" ]] && { err "缺少 --name"; exit 1; }
    SRC="${TRASH}/${N}"
    if [[ ! -d "$SRC" ]]; then
      err "回收站中无此任务: ${N}"
      echo "  查看已移除: sw list --trash"
      exit 1
    fi
    if [[ -d "${TASKS}/${N}" ]]; then
      err "同名任务已存在: ${N}，请先移除现有任务"
      exit 1
    fi
    mv "$SRC" "${TASKS}/${N}"
    # 清除 removed_at 标记
    STF="${TASKS}/${N}/.state"
    if [[ -f "$STF" ]] && grep -q '^removed_at:' "$STF" 2>/dev/null; then
      sed -i '' '/^removed_at:/d' "$STF"
    fi
    ok "任务已恢复: ${N}"
    echo "  继续: sw resume --name=${N}"
    # 清理空 .trash 目录
    rmdir "$TRASH" 2>/dev/null || true
    ;;

  list)
    # 支持 --trash 选项查看已移除的任务
    SHOW_TRASH=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --trash) SHOW_TRASH=1; shift ;;
        *) shift ;;
      esac
    done
    if [[ $SHOW_TRASH -eq 1 ]]; then
      echo "已移除任务 (.trash):"
      FOUND=0
      if [[ -d "$TRASH" ]]; then
        for d in "$TRASH"/*/; do
          [[ -d "$d" ]] || continue
          t=$(basename "$d")
          f="${d}.state"
          if [[ -f "$f" ]]; then
            s=$(grep '^stage:' "$f" | awk -F'"' '{print $2}')
            r=$(grep '^removed_at:' "$f" 2>/dev/null | awk -F': ' '{print $2}')
            printf "  %-30s  %-20s  移除于: %s\n" "$t" "$s" "${r:-N/A}"
            FOUND=1
          fi
        done
      fi
      [[ $FOUND -eq 0 ]] && echo "  (回收站为空)"
    else
      echo "活跃任务:"
      FOUND=0
      for d in "$TASKS"/*/; do
        [[ -d "$d" ]] || continue
        t=$(basename "$d")
        # 跳过 .trash 等隐藏目录
        [[ "$t" == .* ]] && continue
        f="${d}.state"
        if [[ -f "$f" ]]; then
          s=$(grep '^stage:' "$f" | awk -F'"' '{print $2}')
          printf "  %-30s  %s\n" "$t" "$s"
          FOUND=1
        fi
      done
      [[ $FOUND -eq 0 ]] && echo "  (无活跃任务)"
    fi
    ;;

  *) usage ;;
esac
