#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workspace/tasks/${TASK_NAME}"
FILE="${TASK_DIR}/05-archive.md"
echo "[Hard Check] 05-archive 门禁..."

# shellcheck source=hooks/lib_run_tests.sh
. "$(dirname "$0")/lib_run_tests.sh"

[ ! -f "$FILE" ] && echo "❌ 找不到 $FILE" && exit 1
grep -q "## Memory" "$FILE" || echo "⚠️ 缺少 Memory 章节"
grep -q "## Retro" "$FILE" || echo "⚠️ 缺少 Retro 章节"

# README 一致性 —— 查的是**任务产出仓库**的 README，不是 harness 自己的。
#
# 改前是裸 `git diff HEAD`（无 -C），而钩子的 cwd 是 harness 根目录，
# 于是这条检查从写下的第一天起就在看 harness-flow 的 README
# （docs/design/A1-task-git-repo.md 的 1.1）。
TARGET_DIR=$(resolve_target_dir "$TASK_NAME")
if [ -z "$TARGET_DIR" ] || [ ! -d "$TARGET_DIR" ]; then
    echo "⚠️ 跳过 README 检查：任务目标目录不存在 (${TARGET_DIR:-未设置})"
elif [ ! -d "$TARGET_DIR/.git" ]; then
    # 没有基线仓库就无法判定 —— 说明「查不了」，不说「查过没问题」。
    echo "⚠️ 跳过 README 检查：${TARGET_DIR} 不是 git 仓库（基线未建立）"
elif [ -f "$TARGET_DIR/README.md" ]; then
    CHANGES=$(git -C "$TARGET_DIR" diff HEAD --name-only | grep README.md || true)
    STAGED=$(git -C "$TARGET_DIR" diff --staged --name-only | grep README.md || true)
    UNTRACKED=$(git -C "$TARGET_DIR" ls-files --others --exclude-standard | grep README.md || true)
    [ -z "$CHANGES" ] && [ -z "$STAGED" ] && [ -z "$UNTRACKED" ] \
        && echo "⚠️ README.md 未更新，请确认"
else
    echo "⚠️ ${TARGET_DIR} 下没有 README.md，请确认是否需要"
fi

echo "[Hard Check] ✅ 通过"
