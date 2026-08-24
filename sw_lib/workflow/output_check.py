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
from typing import List, Optional, Tuple

from ..core.config import TASKS
from . import stage_state

#: 产出区里出现这些就算占位符，不算内容。
_PLACEHOLDER_RE = re.compile(r"_{3,}|\bTODO\b|\bFIXME\b|\bTBD\b")

#: 任意 nonce 的围栏起始标记 —— 用来发现 harness 未签发的伪造围栏。
#: 刻意比 `stage_state._FENCE_START_RE` 宽（那边要求 8+ 位十六进制）：
#: 这里要抓的正是「写得不像真 nonce」的伪造，判据严了反而漏。
_FENCE_ANY_RE = re.compile(r"<!-- sw:ai-output:start ([0-9a-zA-Z]+) -->")

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


#: `hook-01-01` 的准出阈值：产出区里的歧义分数低于它就不许进 02。
#:
#: 取 8 是照抄 `hooks/01-brainstorming.md` 的原文（「Score < 8 → block
#: Planning entry」）—— 那条规则写了很久，只是从来没有人读它。
#: 两边的一致性由 `test_threshold_matches_documented_hook` 钉住：
#: 文档说 8、代码用 5 是最坏的情形，那会让「真实标准是什么」无人知晓。
#:
#: **不得为了让存量任务或 mock 变绿而放宽**（A6 的 9.3 同款纪律）。
AMBIGUITY_THRESHOLD = 8

#: 歧义分数的几种写法。agent 不会照抄我们脑子里的格式，只认一种等于没接线。
#:
#: 分数在前、范围提示可选、分隔符任意（`：` / `:` / 空格）、
#: 后缀可以是 `/10` 或 `分`。刻意**不**用一条大正则 —— 那种写法出错时
#: 无从判断是哪一种形态没匹配上。
_SCORE_PATTERNS: Tuple[re.Pattern, ...] = (
    # `歧义分数：9` / `Ambiguity Score: 7` / `- 歧义分数 (0-10): 5`
    re.compile(r"(?:歧义分数|歧义度|Ambiguity\s*Score|Score)"
               r"\s*(?:\([^)]*\))?\s*[：:]?\s*"
               r"(\d{1,2})\s*(?:/\s*10|分)?", re.IGNORECASE),
    # `**歧义分数**：6 分` / `__歧义度__: 8` —— markdown 的强调符夹在
    # 关键词与分隔符之间。上一条认不出它：`**` 出现在 `[：:]` 之前，
    # 而那一位只允许空白。agent 写 markdown 是常态，不是例外。
    re.compile(r"(?:\*\*|__|\*|`)\s*"
               r"(?:歧义分数|歧义度|Ambiguity\s*Score|Score)"
               r"\s*(?:\*\*|__|\*|`)\s*"
               r"(?:\([^)]*\))?\s*[：:]?\s*"
               r"(\d{1,2})\s*(?:/\s*10|分)?", re.IGNORECASE),
    # `**歧义分数：** 9/10` —— 分隔符被**包在强调符里面**。
    # 这是任务 helloworld 现场 agent 的真实写法，上面两条都认不出：
    # 第一条要求关键词后直接跟分隔符（这里跟的是 `：**`），
    # 第二条要求关键词后直接跟强调符（这里跟的是 `：`）。
    # 与其继续堆形态，这一条把「关键词与数字之间的强调符/分隔符/空白」
    # 整段放宽 —— 但**不允许**出现其它文字，否则会把
    # 「歧义分数一节里提到 3 个风险」之类的句子误当成分数。
    re.compile(r"(?:\*\*|__|\*|`)?\s*"
               r"(?:歧义分数|歧义度|Ambiguity\s*Score|Score)"
               r"[\s：:*_`]{0,8}"
               r"(\d{1,2})\s*(?:/\s*10|分)?", re.IGNORECASE),
)


def read_ambiguity_score(task: str, stage: str) -> Optional[int]:
    """从**产出区**读 agent 自报的歧义分数。读不到返回 `None`。

    `None` 与 `0` 必须分开：0 是「歧义极高」这个具体结论，
    读不到是「无从判断」。写 `except: return 0` 会把解析失败伪装成
    一个结论，反过来 `return 10` 更糟 —— 那是把未验证说成已达标
    （A6 的第 3 条「不猜数字」）。

    **只读产出区，不读模板区。** 模板区现在是 agent 可写的（本轮放行），
    拿它当判据等于让 agent 自己给自己打分 —— A0 的 2.9.7 已判过同一个错：
    `check_02` 拿模板自带的标题当判据，判据恒真。
    """
    region = read_output_region(task, stage)
    if not region:
        return None
    for pattern in _SCORE_PATTERNS:
        for m in pattern.finditer(region):
            try:
                value = int(m.group(1))
            except (TypeError, ValueError):
                continue
            # 超出 0-10 的数字不是分数，是 agent 写错了。当真比忽略更危险：
            # 「42 分」会被判成远超阈值，直接放行。
            if 0 <= value <= 10:
                return value
    return None


def _ambiguity_problems(task: str, stage: str) -> List[str]:
    """歧义分数判据（`hook-01-01`）。只作用于 01 阶段。

    这条规则在 `hooks/01-brainstorming.md` 里写了很久 ——
    「Score < 8 → block Planning entry」—— 但全仓库检索 `ambiguity`
    只找到一个字段定义，**零消费方**。于是它在真实流程中不存在，
    这是「判据存在、无人调用」的第七例。

    接上它同时解决另一件事：agent 的收敛条件。任务 `ppppp` 问了 14 轮
    （`hook-01-02` 只写「≥3 questions」，无上界），因为「信息够了吗」
    没有可执行的判据。分数达标才是**语义**收敛条件 —— 而不是凑够轮数。
    """
    if stage != "01-brainstorming":
        return []

    score = read_ambiguity_score(task, stage)
    if score is None:
        return [
            "❌ 产出区未给出歧义分数（hook-01-01 要求 0-10 的自评）",
            "   分数不可得**不算达标** —— 不写就能跳过判据是最省事的绕过方式。",
            "   请在产出里写明：`歧义分数：<0-10>`，并说明为什么是这个分数。",
        ]

    if score < AMBIGUITY_THRESHOLD:
        return [
            f"❌ 歧义分数 {score} 低于准出阈值 {AMBIGUITY_THRESHOLD}"
            "（hook-01-01：不带假设进入下一阶段）",
            "   下一步是**继续用 `question` 工具澄清**尚未确定的点，"
            "然后重新给出分数。",
            "   **不要**直接把数字改大 —— 分数是澄清程度的度量，不是准出开关。",
        ]
    return []


def check_tamper(task: str, stage: str) -> List[str]:
    """检出对**围栏**与 **Gate** 的篡改，返回问题清单（空表示干净）。

    存在的前提是阶段文件现在可写（见 `agents/opencode._stage_file_write_rules`）：
    放行模板回填解开了 agent 的收敛出口 —— 任务 `ppppp` 那 14 轮 question
    的根因就是它没有任何终止动作可执行 —— 但同时把两样东西暴露了出来。

    **只盯这两处，模板正文一律不管。** 把「文件被改过」当成违规等于把刚
    放开的写权限又收回去，agent 依旧无处收敛。判据必须精确到「哪一部分
    不是它的」，而不是「它动过这个文件」。

    真相源全在 `.state`（`output_nonce` 与 `gate`，受 HMAC 覆盖），
    agent 改文件动不了它 —— 这是本校验能成立的根据。
    """
    path = TASKS / task / f"{stage}.md"
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    problems: List[str] = []
    expected = stage_state.read_output_nonce(task, stage)

    if expected:
        start, end = stage_state.fence_markers(expected)
        if start not in content or end not in content:
            # `.state` 记着 nonce 说明围栏曾由 sw 写下，现在不见了。
            # 这与「本阶段没产出」是两回事：那种情况 `.state` 里也不会有
            # nonce。区分开才能给出正确的下一步（追查 vs 重跑）。
            problems.append(
                "❌ 产出区围栏被删除或改写 —— `.state` 记录的 nonce "
                f"`{expected}` 在文件中已不完整。围栏由 harness 写入，"
                "标记「哪段是 agent 说的」，不得由 agent 改动。")

        # 别的 nonce 出现即为伪造：agent 猜不到真 nonce，写得出来只有编造。
        # 特别防「在真围栏之前另插一段假围栏」—— `split_output_region`
        # 取第一个 start，那样判据就会去读伪造的那一段。
        others = {m.group(1) for m in _FENCE_ANY_RE.finditer(content)
                  if m.group(1) != expected}
        if others:
            problems.append(
                "❌ 文件中存在 harness 未签发的产出区 nonce: "
                f"{', '.join(sorted(others))} —— nonce 由 harness 随机签发，"
                "伪造它等于把自己写的文本冒充成已记录的产出。")

    problems.extend(_gate_tamper(task, stage, content))
    return problems


def _gate_tamper(task: str, stage: str, content: str) -> List[str]:
    """文件里的 Gate 勾选与 `.state` 的签署状态是否一致。

    判定不会被文件骗（`check_01` 读 `sw state get`），所以这条查的是
    **显示层的谎**：用户看的是文件，一个伪造的 `[x] Design approved`
    会让人以为门禁已过。真相源在 `.state`，因此对照是确定的。

    只在「文件勾了、`.state` 没签」这一个方向上报错。反方向（已签署但
    文件还没渲染）是正常的中间态 —— `render_gate_section` 是事后调用的。
    """
    gate = stage_state.read_gate(task, stage)
    if gate.signed_by:
        return []

    forged = [item.label for item in gate.items
              if item.label and _is_checked_in_text(content, item.label)]
    if not forged:
        return []
    return [
        "❌ Gate 在文件中被勾选，但 `.state` 里并未签署 —— "
        f"伪造的签署项: {', '.join(forged)}。"
        "Gate 由用户在 TUI 中批准，签署状态只存在 `.state` 里；"
        "文件是它的映像，改文件不会让门禁通过，只会误导读文件的人。",
    ]


def _is_checked_in_text(content: str, label: str) -> bool:
    """文本里是否存在 `- [x] <label>` 这样的勾选行。"""
    pattern = r"^\s*[-*]\s*\[[xX]\]\s*" + re.escape(label.strip())
    return re.search(pattern, content, re.MULTILINE) is not None


def check_output(task: str, stage: str) -> OutputVerdict:
    """产出区必须存在且有实质内容，且围栏与 Gate 未被篡改。

    篡改**先查**。顺序是刻意的：围栏被伪造时，从围栏里读出来的「实质内容」
    毫无意义 —— 先报「你改了围栏」比先报一堆字符数有信息量得多
    （与 A2 的 `_check_03b` 里「哈希先查、测试后跑」同一条纪律）。
    """
    tamper = check_tamper(task, stage)
    if tamper:
        return OutputVerdict(False, tamper + [
            "   模板正文可以自由回填 —— 被拒的只是围栏与 Gate 这两处。",
            "   请把产出内容写在回复正文里，harness 会落进产出区。",
        ])

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

    # 歧义分数放在最后：内容都没有时先说「空转」更有信息量，
    # 报「分数没写」会让人以为只差一个数字。
    ambiguity = _ambiguity_problems(task, stage)
    if ambiguity:
        return OutputVerdict(False, ambiguity)

    lines = [f"✅ {stage} 产出区有实质内容（{len(substance)} 字符）"]
    score = read_ambiguity_score(task, stage)
    if score is not None:
        lines.append(f"   歧义分数 {score} ≥ 阈值 {AMBIGUITY_THRESHOLD}")
    return OutputVerdict(True, lines)


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
