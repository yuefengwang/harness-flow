#!/usr/bin/env bash
# sw 端到端自动化测试 — 模拟用户完整工作流
# 用法: bash test_e2e.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
PASS=0; FAIL=0

assert_ok() {
    local desc="$1"; shift
    if "$@" &>/dev/null; then
        ((PASS++)) || true
        echo -e "  ${GREEN}[✓]${NC} $desc"
    else
        ((FAIL++)) || true
        echo -e "  ${RED}[✗]${NC} $desc  (cmd: $*)"
    fi
}

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        ((PASS++)) || true
        echo -e "  ${GREEN}[✓]${NC} $desc"
    else
        ((FAIL++)) || true
        echo -e "  ${RED}[✗]${NC} $desc"
        echo "       expected: $expected"
        echo "       actual:   $actual"
    fi
}

SW="python3 $(cd "$(dirname "$0")" && pwd)/sw"
TASKS="$(cd "$(dirname "$0")" && pwd)/workflow/tasks"

# ── cleanup ──
rm -rf "$TASKS"/e2e-test "$TASKS"/.trash 2>/dev/null || true

echo ""
echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   sw 端到端自动化测试                ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

# ── 1. init ──
echo -e "${BLUE}── 1. sw init (创建任务 + 需求上下文) ──${NC}"
$SW init --name=e2e-test --context="实现用户认证模块：支持JWT登录，包含注册/登录/刷新token" --agent=opencode 2>&1 | head -5
assert_ok "task dir created" test -d "$TASKS/e2e-test"
assert_ok ".state exists" test -f "$TASKS/e2e-test/.state"
assert_ok ".context exists" test -f "$TASKS/e2e-test/.context"
assert_ok ".input exists" test -f "$TASKS/e2e-test/.input"
assert_ok "templates copied" test -f "$TASKS/e2e-test/01-brainstorming.md"
CTX=$(cat "$TASKS/e2e-test/.context")
assert_eq "context stored" "实现用户认证模块：支持JWT登录，包含注册/登录/刷新token" "$CTX"

# ── 2. status ──
echo ""
echo -e "${BLUE}── 2. sw status ──${NC}"
$SW status --name=e2e-test 2>&1
STATUS_OUT=$($SW status --name=e2e-test 2>&1)
assert_ok "status shows pending" echo "$STATUS_OUT" | grep -q "pending"

# ── 3. advance reject (pending) ──
echo ""
echo -e "${BLUE}── 3. sw advance (pending → reject) ──${NC}"
if $SW advance --name=e2e-test 2>&1; then
    echo -e "  ${RED}[✗]${NC} advance should have been rejected"
    ((FAIL++)) || true
else
    echo -e "  ${GREEN}[✓]${NC} advance correctly rejected (pending stage)"
    ((PASS++)) || true
fi

# ── 4. next (start stage 01) ──
echo ""
echo -e "${BLUE}── 4. sw next (启动阶段 + 注入上下文) ──${NC}"
printf 'y\n' | $SW next --name=e2e-test 2>&1 | head -5
assert_ok "stage_status = in_progress" grep -q "in_progress" "$TASKS/e2e-test/.state"
assert_ok "context injected to .input" grep -q "用户认证" "$TASKS/e2e-test/.input"
assert_ok ".log has sw events" test -s "$TASKS/e2e-test/.log"

# ── 5. answer (simulate agent interaction) ──
echo ""
echo -e "${BLUE}── 5. sw answer (用户回复 Agent) ──${NC}"
$SW answer --name=e2e-test --text="选择方案A: JWT + refresh token 双token机制" 2>&1
assert_ok "answer in .input" grep -q "方案A" "$TASKS/e2e-test/.input"
assert_ok "answer in .log" grep -q "user.*选择方案A" "$TASKS/e2e-test/.log"
echo ""
$SW answer --name=e2e-test --text="使用bcrypt加密，token过期时间2小时" 2>&1
assert_ok "answer 2 in .input" grep -q "bcrypt" "$TASKS/e2e-test/.input"

# ── 6. advance (validate + push to stage 02) ──
echo ""
echo -e "${BLUE}── 6. sw advance --force (推进到 02-规划) ──${NC}"
$SW advance --name=e2e-test --force 2>&1 | tail -3
assert_ok "advanced to 02-planning" grep -q "02-planning" "$TASKS/e2e-test/.state"
assert_ok "stage_status reset to pending" grep -q "pending" "$TASKS/e2e-test/.state"

# ── 7. next (stage 02, auto-inject prev stage output) ──
echo ""
echo -e "${BLUE}── 7. sw next stage 02 (自动注入前序产出) ──${NC}"
printf 'y\n' | $SW next --name=e2e-test 2>&1 | head -5
assert_ok "prev stage injected to .input" grep -q "前一阶段产出" "$TASKS/e2e-test/.input"
assert_ok "01-brainstorming.md content in .input" grep -q "设计文档" "$TASKS/e2e-test/.input"
INPUT_LINES=$(wc -l < "$TASKS/e2e-test/.input" | tr -d ' ')
assert_ok ".input has accumulated context (>10 lines)" test "$INPUT_LINES" -gt 10

# ── 8. rapid advance to stage 05 ──
echo ""
echo -e "${BLUE}── 8. 快速推进到 05-归档 ──${NC}"
for stage in 2 3 4; do
    $SW advance --name=e2e-test --force 2>&1 | grep "━━━" | head -1 || true
    printf 'y\n' | $SW next --name=e2e-test 2>&1 | grep "━━━" | head -1 || true
done
$SW advance --name=e2e-test --force 2>&1 | grep -E "━━━|已完成" | head -1 || true
assert_ok "reached stage 05-archive" grep -q "05-archive" "$TASKS/e2e-test/.state"

# ── 9. remove + restore ──
echo ""
echo -e "${BLUE}── 9. sw remove → sw restore ──${NC}"
$SW remove --name=e2e-test 2>&1 | grep "✓"
assert_ok "task moved to trash" test -d "$TASKS/.trash/e2e-test"
assert_ok "task gone from active" test ! -d "$TASKS/e2e-test"

$SW restore --name=e2e-test 2>&1 | grep "✓"
assert_ok "task restored" test -d "$TASKS/e2e-test"

# ── cleanup ──
rm -rf "$TASKS"/e2e-test "$TASKS"/.trash
python3 -c "
c = open('workflow/STATUS.md').read()
import re
c = re.sub(r'\*\*活动任务:\*\*\s*.*', '**活动任务:** 无', c)
c = re.sub(r'\*\*当前阶段:\*\*\s*.*', '**当前阶段:** N/A', c)
open('workflow/STATUS.md','w').write(c)
"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  结果: ${PASS} passed, ${FAIL} failed              ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"

[[ $FAIL -eq 0 ]] || exit 1
