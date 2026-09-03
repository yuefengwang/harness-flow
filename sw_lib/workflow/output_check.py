"""阶段产出区的实质性检查（01/02 阶段的门禁判据）。

任务 `helloworld` 的现场：01 与 02 都过闸并推进到 03，两个阶段文件的模板区
全是 `___` 与空表格。`check_01` 只验「文件存在 + gate 已签署」，
`check_02` 只 `grep -q "## Task DAG"` —— 那个标题是模板自带的，判据恒真。

判据**以产出区为主、模板区为备**（`_substance_report`）：

* 围栏区（`<!-- sw:ai-output:start <nonce> -->` 之内）由 sw 写入、
  nonce 不可预测，「哪段是 agent 说的」是确定的，所以它优先；
* 模板区回落是任务 `rrr` 逼出来的：放行阶段文件写权限之后，模板区成了
  agent 的**正式落点**（02 的 prompt 明确要求它回填）。`rrr` 的 agent
  照做了 —— 7 个任务的 DAG 全在模板区 —— 然后又调了一次 `question`，
  最终回复只剩一句 39 字符的收尾话，围栏区因此只收到那一句，
  判据报「仅 30 字符」把它拦下。判据只量一个来源，而产出有两个落点。

回落**不是**「量整篇」。02 模板自带的标题与 `- **Method**: unit /
integration / manual` 这类样板文字去噪后早已超过阈值，量整篇会让判据恒真
—— 那正是 helloworld 那次的错。所以回落先减去发货模板里已有的行
（`_added_lines`），只算 agent 真正添进去的内容。

与 A2 的 `has_code_output` 同一条纪律：空转的代价必须在本阶段暴露，
而不是推迟到下游才发现，然后来回返工。

与 `fact_pack.extract_claims_from_stage_file` 是同一套语义（围栏优先、
模板回落）—— A0 的 2.9.11 第 1 条已经在 claims 上判过这个形状，
那次只修了 claims，没有回头检查本模块。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..core.config import TASKS, TPLS
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


def read_template_region(task: str, stage: str) -> Optional[str]:
    """取出围栏**之外**的模板区正文；文件不存在返回 None。

    Gate 区一并去掉：那是 `render_gate_section` 从 `.state` 渲染出来的，
    不是 agent 写的。把 harness 自己的输出算进「agent 的产出」，
    等于让判据给自己打分。
    """
    path = TASKS / task / f"{stage}.md"
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8", errors="replace")

    parts = stage_state.split_output_region(content)
    if parts is None:
        outside = content
    else:
        before, after = parts
        outside = before + after

    return _strip_gate_section(outside)


def _strip_gate_section(text: str) -> str:
    """去掉 `## Gate` 区（到下一个二级标题为止）。"""
    out: List[str] = []
    in_gate = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_gate = line.startswith("## Gate")
        if not in_gate:
            out.append(line)
    return "\n".join(out)


def _template_lines(stage: str) -> frozenset:
    """发货模板里的行（去噪后）。读不到模板就返回空集合。

    读 `templates/{stage}.md` 而不是任务目录里的副本：任务副本已经被
    agent 改过，拿它当基准等于拿被测对象当尺子。模板是我们维护的、
    agent 碰不到的文件。
    """
    tpl = TPLS / f"{stage}.md"
    if not tpl.is_file():
        return frozenset()
    try:
        content = tpl.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    return frozenset(
        n for n in (_strip_noise(line) for line in content.splitlines()) if n)


def _added_lines(region: str, stage: str) -> str:
    """模板区里 agent **新增**的部分（逐行减去发货模板已有的行）。

    为什么必须相消：02 模板自带 `## Task DAG` / `## Test Strategy` /
    `## Tech Detail` 三个标题与 `- **Method**: unit / integration / manual`
    这类样板，去噪后合计已超过阈值 80。不减就等于「文件存在即通过」——
    A0 的 2.9.7 判过这个错（`check_02` 拿模板自带的标题当判据，判据恒真）。

    逐行相消而不是整段 diff：agent 通常是**就地替换** `___`
    （`- **Do**: ___` → `- **Do**: 创建项目结构...`），行的位置与数量都会变，
    但「这一行在模板里原样出现过吗」是确定的。
    """
    known = _template_lines(stage)
    added = []
    for line in region.splitlines():
        noise_free = _strip_noise(line)
        if noise_free and noise_free not in known:
            added.append(noise_free)
    return "".join(added)


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
    # `歧义分数达到 8` / `歧义分数已达到 9` / `歧义分数为 7` / `歧义分数是 6`
    # —— 关键词与数字之间夹着一个**中文动词**。上面三条都认不出：它们只允许
    # 空白、分隔符与强调符，动词的两个字卡在中间。
    #
    # 任务 maybework 的现场：agent 写了「歧义分数达到 8」，判据读出 None，
    # 门禁报「未给出歧义分数」把它拦下。**agent 没做错任何事**，是判据只认
    # 我们脑子里的那种格式。判据自己的缺陷不该由被判者承担。
    #
    # 动词白名单而不是通配：`.{0,4}` 那种写法会把「歧义分数一节里提到 3 个
    # 风险」读成 3 —— 判据读到假数字比读不到更危险。
    re.compile(r"(?:\*\*|__|\*|`)?\s*"
               r"(?:歧义分数|歧义度|Ambiguity\s*Score|Score)"
               r"(?:\*\*|__|\*|`)?\s*"
               r"(?:已)?(?:达到|评为|评估为|定为|为|是)\s*"
               r"[：:]?\s*"
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


def _ambiguity_advisory(task: str, stage: str) -> List[str]:
    """歧义自评（`hook-01-01`）：**记账 + 提示，不拦**。只作用于 01 阶段。

    ## 为什么它不再硬拦（A13）

    歧义分数是 agent **自评**，不是 harness 观测到的事件。拿它当硬拦判据，
    等于把准出开关交给被判者 —— 它想不写就不写，而门禁只会说「你没写 X」。

    三个真实任务收到**同一句**「❌ 产出区未给出歧义分数」，根因各不相同：

    * `maybework` —— 写了「歧义分数达到 8」，正则不认「达到」（判据的错）；
    * `7090` —— agent 在正文里问「你同意方案 A 吗」就停了，阶段没走完；
    * `ppppp` —— 问了 14 轮无收敛出口。

    一句话指向三个完全不同的下一步，这是「失败信息指错方向」（2.9.16）的
    01 版本。真正拦住空转的从来是**硬规则**（产出区实质内容 ≥ 80 字符），
    它读的是 harness 落盘的围栏区，agent 绕不过去。

    ## 放行必须留痕

    返回提示行的同时写 `.state`。读不到分数记 `unavailable`（❓），
    **不伪造成 ✅** —— 与 A2 的 `--abandon-witness`、非 Python 栈让路
    同一条纪律：让路可以，让路而不记账不行。

    ## 低分为什么是「继续提问」而不是「改数字」

    分数是澄清程度的度量。低于阈值说明还有没问清的地方，下一步是
    继续用 `question` 澄清 —— 01 阶段的 agent 确实有这个工具（Q2 已核）。
    把数字改大只会让 02 带着未澄清的假设开工。
    """
    if stage != "01-brainstorming":
        return []

    score = read_ambiguity_score(task, stage)

    if score is None:
        stage_state.record_ambiguity(
            task, stage, stage_state.AMBIGUITY_UNAVAILABLE,
            reason="产出区中未找到 0-10 的歧义自评")
        return [
            "❓ 产出区未给出歧义分数 —— 本项**未测到**，不计为通过",
            "   自评不是硬门禁（A13：硬规则决定准出，自评只作参考），"
            "因此不拦；但它已记为 unavailable，下游报告不会显示 ✅。",
            "   若设计尚有不确定处，请继续用 `question` 工具澄清，"
            "并在产出里写明 `歧义分数：<0-10>`。",
        ]

    if score < AMBIGUITY_THRESHOLD:
        stage_state.record_ambiguity(
            task, stage, stage_state.AMBIGUITY_BELOW, score=score,
            reason=f"自评 {score} 低于目标 {AMBIGUITY_THRESHOLD}")
        return [
            f"❓ 歧义分数 {score} 低于目标 {AMBIGUITY_THRESHOLD}"
            " —— 设计仍有未澄清处（hook-01-01）",
            "   自评不硬拦，但这一项记为 below_threshold，不是 ✅。",
            "   下一步是**继续用 `question` 工具逐个澄清**尚未确定的点，"
            f"问到自评达到 {AMBIGUITY_THRESHOLD} 为止。",
            "   **不要**直接把数字改大 —— 分数是澄清程度的度量，不是准出开关。",
        ]

    stage_state.record_ambiguity(
        task, stage, stage_state.AMBIGUITY_OK, score=score)
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

    count, where = _substance_report(task, stage, region)
    if count < _MIN_SUBSTANCE:
        return OutputVerdict(False, [
            f"❌ {stage} 的产出区没有实质内容"
            f"（去掉占位符与格式符后仅 {count} 字符，"
            f"至少需要 {_MIN_SUBSTANCE}）",
            "   占位符（`___` / TODO / FIXME）不算内容。",
            "   两个落点都查过了：回复正文（产出区）与模板区回填。",
            "   请让 agent 给出本阶段真正的结论后再签署门禁。",
        ])

    # 到这里硬规则已全部通过。硬规则**决定**准出，自评只**参考**（A13）。
    #
    # 顺序是刻意的：内容都没有时先说「空转」更有信息量，报「分数没写」
    # 会让人以为只差一个数字 —— 三个任务正是这样被推去补数字的。
    lines = [f"✅ {stage} {where}有实质内容（{count} 字符）"]

    advisory = _ambiguity_advisory(task, stage)
    if advisory:
        # 放行，但把 ❓ 带在通过结论后面。不合并进 ✅ 那一行：
        # 二态化的第一步就是把「未测到」写进「通过」的句子里。
        return OutputVerdict(True, lines + advisory)

    score = read_ambiguity_score(task, stage)
    if score is not None:
        lines.append(f"   歧义分数 {score} ≥ 目标 {AMBIGUITY_THRESHOLD}")
    return OutputVerdict(True, lines)


def _substance_report(task: str, stage: str, region: str) -> Tuple[int, str]:
    """实质内容的字符数与它的来源。**围栏区优先，模板区回落。**

    顺序不能反，理由与 `fact_pack.extract_claims_from_stage_file` 相同：
    围栏区由 sw 落盘、nonce 不可预测，可信度高于 agent 可任意改写的模板区。
    若模板区优先，agent 在模板里写一份好看的、在回复里写另一份，
    判据会读到前者。

    只在围栏区**不足**时才看模板区 —— 不是两处相加。相加会让两段各自都
    达不到标准的碎片凑够阈值，而「产出够不够」问的是有没有一份完整交付，
    不是总字数。
    """
    fenced = len(_strip_noise(region))
    if fenced >= _MIN_SUBSTANCE:
        return fenced, "产出区"

    template = read_template_region(task, stage)
    if not template:
        return fenced, "产出区"

    added = len(_added_lines(template, stage))
    if added > fenced:
        return added, "模板区（agent 回填）"
    return fenced, "产出区"


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
