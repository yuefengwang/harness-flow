"""阶段产出区的实质性检查（01/02 阶段的门禁判据）。

任务 `helloworld` 的现场：01 与 02 都过闸并推进到 03，两个阶段文件的模板区
全是 `___` 与空表格。`check_01` 只验「文件存在 + gate 已签署」，
`check_02` 只 `grep -q "## Task DAG"` —— 那个标题是模板自带的，判据恒真。

判据落在**产出区**（`<!-- sw:ai-output:start <nonce> -->` 围栏内）而不是
模板区，这是刻意的：

* 围栏由 sw 写入、nonce 不可预测，「哪段是 agent 说的」是确定的；
* 模板区 agent 既不该也不能改 —— 硬层对 `workspace/**` 的 write 一律 deny，
  prompt 里那条「用 write_file 回填模板」的指令成功率恒为 0（本轮已删）。
  拿模板区的 `___` 当判据，等于要求 agent 做一件被禁止的事。

与 A2 的 `has_code_output` 同一条纪律：空转的代价必须在本阶段暴露，
而不是推迟到下游才发现，然后来回返工。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from ..core.config import TASKS
from . import stage_state

#: 产出区里出现这些就算占位符，不算内容。
_PLACEHOLDER_RE = re.compile(r"_{3,}|\bTODO\b|\bFIXME\b|\bTBD\b")

#: 去掉占位符、标点与空白后，实质内容的最低字符数。
#:
#: 取 80 的依据是实测样本：helloworld 那次真实的 01 产出（内容齐全、
#: 只是没回填模板区）去噪后约 300 字符，而「产出区为空」「只抄了几行
#: 占位符」这两种空转都在 30 以下。80 落在两者之间且离两侧都远。
#: 这个阈值**不得为了让存量任务变绿而放宽**（A6 的 9.3 同款纪律）。
_MIN_SUBSTANCE = 80


@dataclass
class OutputVerdict:
    ok: bool
    lines: List[str]


def _strip_noise(text: str) -> str:
    """去掉占位符、markdown 结构符与空白，留下疑似真实内容的部分。"""
    text = _PLACEHOLDER_RE.sub("", text)
    # markdown 结构符：标题、列表、表格、强调、代码围栏
    text = re.sub(r"[#*`|>\-\[\]()]+", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def read_output_region(task: str, stage: str) -> Optional[str]:
    """取出阶段文件里的产出区正文；没有产出区返回 None。

    刻意复用 `stage_state.split_output_region` 的围栏定位规则，
    而不是自己再写一遍正则 —— 两份实现会各自漂移。
    """
    path = TASKS / task / f"{stage}.md"
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8", errors="replace")
    parts = stage_state.split_output_region(content)
    if parts is None:
        return None
    before, after = parts
    # split_output_region 给的是「区前 / 区后」，产出区本体是中间那段
    body = content[len(before):len(content) - len(after)]
    # 去掉围栏行本身与标题
    body = re.sub(r"<!-- sw:ai-output:(start|end) [0-9a-f]+ -->", "", body)
    body = body.replace(stage_state.AI_OUTPUT_HEADING, "")
    return body


def check_output(task: str, stage: str) -> OutputVerdict:
    """产出区必须存在且有实质内容。"""
    region = read_output_region(task, stage)

    if region is None:
        return OutputVerdict(False, [
            f"❌ {stage} 没有 AI 产出区 —— 本阶段未产生任何内容（空转）",
            "   产出区由 harness 在 agent 回复后自动写入；它不存在说明 agent",
            "   这一轮什么都没说，或运行中途失败了。",
            "   请重新运行本阶段，确认 agent 给出了实质产出后再签署门禁。",
        ])

    substance = _strip_noise(region)
    if len(substance) < _MIN_SUBSTANCE:
        return OutputVerdict(False, [
            f"❌ {stage} 的产出区没有实质内容"
            f"（去掉占位符与格式符后仅 {len(substance)} 字符，"
            f"至少需要 {_MIN_SUBSTANCE}）",
            "   占位符（`___` / TODO / FIXME）不算内容。",
            "   请让 agent 给出本阶段真正的结论后再签署门禁。",
        ])

    return OutputVerdict(True, [
        f"✅ {stage} 产出区有实质内容（{len(substance)} 字符）",
    ])


_USAGE = "用法: python3 -m sw_lib.workflow.output_check <task-name> <stage>"


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 入口：退出码 0 = 放行，1 = 拒绝。原因一律打印到 stdout。"""
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 2:
        print(_USAGE, file=sys.stderr)
        return 2

    verdict = check_output(args[0], args[1])
    for line in verdict.lines:
        print(line)
    return 0 if verdict.ok else 1


if __name__ == "__main__":       # pragma: no cover - CLI 入口
    raise SystemExit(main())
