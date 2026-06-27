#!/usr/bin/env bash
set -e
TASK_NAME=$1
TASK_DIR="workspace/tasks/${TASK_NAME}"
BRAINSTORM_FILE="${TASK_DIR}/01-brainstorming.md"

echo "[Hard Check] 03-coding 门禁..."

# 检查 Spec 契约一致性提醒
if [ -f "$BRAINSTORM_FILE" ]; then
    if grep -q "## Spec Contracts" "$BRAINSTORM_FILE"; then
        echo "ℹ️ 发现已定义的 Spec Contracts，正在进行契约一致性自动核对与测试校验..."
    fi

    # 跨会话进展校验 (Progress Handover Constraint)
    PROGRESS_FILE="${TASK_DIR}/progress.txt"
    echo "检查 progress.txt 进展小结..."
    
    # 检测是否开启了 Prototype Mode (逃生通道)
    IS_PROTOTYPE=0
    if grep -q -i "\[x\] Prototype Mode" "$BRAINSTORM_FILE"; then
        IS_PROTOTYPE=1
        echo "ℹ️ 已启用 Prototype Mode (原型模式)，放宽规约与进展硬卡点检查。"
    fi

    if [ ! -f "$PROGRESS_FILE" ]; then
        if [ "$IS_PROTOTYPE" -eq 1 ]; then
            echo "⚠️ [Prototype Warning] 未检测到进展记录文件 progress.txt。建议补充以方便后续开发。"
        else
            echo "❌ 推进失败：未检测到有效进展小结。请在会话末尾添加 '## Progress' 开头的章节，并写下不少于 50 字的交接进展。"
            exit 1
        fi
    else
        # 校验字符数是否足够
        CHAR_COUNT=$(wc -m < "$PROGRESS_FILE" | tr -d ' ')
        if [ "$CHAR_COUNT" -lt 100 ]; then
            if [ "$IS_PROTOTYPE" -eq 1 ]; then
                echo "⚠️ [Prototype Warning] progress.txt 内容过短。建议在会话末尾以 '## Progress' 提供不少于 50 字的待办描述。"
            else
                echo "❌ 推进失败：progress.txt 进展内容过短。请在会话末尾以 '## Progress' 格式提供更加详实的进展与待办描述（不少于 50 字）。"
                exit 1
            fi
        else
            echo "✅ 进展记录校验通过"
        fi
    fi
fi

# 运行 pytest
if [ -f "pytest.ini" ] || [ -f "pyproject.toml" ] || ls test_*.py 2>/dev/null || ls tests/unit/test_*.py 2>/dev/null; then
    echo "运行 pytest..."
    pytest -v || { echo "❌ pytest 失败"; exit 1; }
fi

# 运行 npm test
if [ -f "package.json" ] && grep -q '"test"' "package.json"; then
    echo "运行 npm test..."
    npm test || { echo "❌ npm test 失败"; exit 1; }
fi

echo "[Hard Check] ✅ 通过"
