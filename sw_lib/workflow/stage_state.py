"""sw_lib.workflow.stage_state — Gate/Route 状态的唯一读写入口。

设计依据：docs/design-json-state-source.md

核心原则：**机器读的东西 agent 不能写，agent 写的东西机器不解析。**

Gate 签署与 Route 决策是「判定依据」，存在 `.state` 的 `stages` 字段里。
Markdown 只承载 agent 产出与人类阅读，不再是任何判定的输入。

历史背景（为什么必须这样）：阶段文件曾同时是产出、签署记录、路由决策和机器状态，
而定位区域的唯一手段是在字符串里找 `## Gate`。agent 的产出不受约束，完全可以
写出这个标记 —— 于是「分隔符」和「内容」共用同一套词汇表，解析必然歧义。
同一个 `## Gate` 在代码里有 4 种定位语义（第一个 / 最后一个 / 逐行首个 / 仅判存在），
写入、签署、校验三方理解不一致，产出了一连串无法根治的 bug：
签署入口不可达、下游待办被算作门禁、Route 重复写入、Gate 区每次 flush 追加一份。
"""

import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import STAGES, TASKS, TPLS
from ..core.state import read_state, update_state, NO_CHANGE


# ── 数据模型 ──

@dataclass
class GateItem:
    """单个门禁项。key 与 label 来自模板，checked 只能由签署入口写。"""
    key: str
    label: str
    checked: bool = False


@dataclass
class GateState:
    items: List[GateItem] = field(default_factory=list)
    signed_by: Optional[str] = None   # "user" | "auto" | None
    signed_at: Optional[str] = None

    @property
    def exists(self) -> bool:
        """是否有可签署的门禁项。"""
        return bool(self.items)

    @property
    def signed(self) -> bool:
        """全部项已勾选。空 Gate 视为未签署，避免「无事可签」被误判为通过。"""
        return bool(self.items) and all(i.checked for i in self.items)

    @property
    def pending(self) -> List[GateItem]:
        return [i for i in self.items if not i.checked]


# ── 内部：.state 读写 ──

def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _stage_bucket(state: Dict[str, Any], stage: str) -> Dict[str, Any]:
    """取得（必要时创建）`stages.{stage}` 子树。就地修改传入的 state。"""
    stages = state.setdefault("stages", {})
    if not isinstance(stages, dict):
        stages = {}
        state["stages"] = stages
    bucket = stages.setdefault(stage, {})
    if not isinstance(bucket, dict):
        bucket = {}
        stages[stage] = bucket
    return bucket


def _read_bucket(task: str, stage: str) -> Dict[str, Any]:
    """只读地取出 `stages.{stage}`，对写坏的 .state 一律降级为空字典。

    `.state` 可能被手工编辑或旧版本写坏（`stages` 变成字符串等）。读路径必须
    降级而不是抛异常：判定逻辑崩溃会让 /advance 直接不可用，比门禁误判更糟。
    """
    state = read_state(task)
    stages = state.get("stages")
    if not isinstance(stages, dict):
        return {}
    bucket = stages.get(stage)
    return bucket if isinstance(bucket, dict) else {}


def _slug(label: str, idx: int) -> str:
    """模板 label -> 稳定 key。

    label 可能含反引号占位符（`04-review` 的 ``Full build: `___` ``）与标点，
    统一压成 ascii 小写下划线；空结果回退到序号，保证 key 始终非空且唯一。
    """
    s = re.sub(r'`[^`]*`', '', label)          # 去掉占位符
    s = re.sub(r'[^0-9A-Za-z]+', '_', s).strip('_').lower()
    return s or f"item_{idx + 1}"


# ── 模板播种 ──

def parse_template_gate(stage: str) -> List[GateItem]:
    """从 templates/{stage}.md 的 `## Gate` 区解析门禁项定义。

    读**模板**而不是任务文件：模板是我们自己维护的、agent 碰不到的文件，
    因此这里的 `## Gate` 定位是安全的 —— 这是全代码库唯一还需要解析
    Markdown Gate 区的地方。
    """
    tpl = TPLS / f"{stage}.md"
    if not tpl.exists():
        return []
    content = tpl.read_text(encoding="utf-8", errors="replace")
    items: List[GateItem] = []
    in_gate = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Gate"):
            in_gate = True
            continue
        if in_gate:
            if stripped.startswith("## "):
                break
            m = re.match(r'-\s*\[[ xX]\]\s*(.+?)\s*$', stripped)
            if m:
                label = m.group(1)
                items.append(GateItem(key=_slug(label, len(items)), label=label))
    return items


def seed_gate(task: str, stage: str) -> bool:
    """把模板的门禁项定义播种进 `.state`。幂等：已有则不动。

    返回是否发生了写入。
    """
    wrote = False

    def mutate(state):
        nonlocal wrote
        if not state:
            return NO_CHANGE
        bucket = _stage_bucket(state, stage)
        if isinstance(bucket.get("gate"), dict) and bucket["gate"].get("items"):
            return NO_CHANGE                     # 幂等：已播种，不刷新时间戳
        items = parse_template_gate(stage)
        bucket["gate"] = {
            "items": [{"key": i.key, "label": i.label, "checked": i.checked}
                      for i in items],
            "signed_by": None,
            "signed_at": None,
        }
        wrote = True
        return state

    update_state(task, mutate)
    return wrote


# ── Gate 读写 ──

def read_gate(task: str, stage: str) -> GateState:
    """读取 Gate 状态。不碰 Markdown。

    `.state` 里没有记录时按模板定义返回未签署状态（不写盘），这样新任务、
    以及 seed 之前的读取都能拿到正确的「待签署」而不是「无门禁」。
    """
    bucket = _read_bucket(task, stage)
    raw = bucket.get("gate")
    if not isinstance(raw, dict):
        return GateState(items=parse_template_gate(stage))
    raw_items = raw.get("items")
    items = [
        GateItem(key=str(d.get("key", f"item_{n + 1}")),
                 label=str(d.get("label", "")),
                 checked=bool(d.get("checked", False)))
        for n, d in enumerate(raw_items)
        if isinstance(d, dict)
    ] if isinstance(raw_items, list) else []
    # 记录存在但不可用（写坏或空）→ 回退模板定义，仍报「待签署」而不是「无门禁」
    if not items:
        return GateState(items=parse_template_gate(stage))
    return GateState(items=items,
                     signed_by=raw.get("signed_by"),
                     signed_at=raw.get("signed_at"))


def sign_gate(task: str, stage: str, by: str = "user") -> bool:
    """签署当前阶段的全部门禁项。返回是否写入成功。

    没有任何门禁项时返回 False —— 调用方需要区分「签署成功」与「无事可签」，
    否则空 Gate 会被静默当成通过。
    """
    signed = False

    def mutate(state):
        nonlocal signed
        if not state:
            return NO_CHANGE
        bucket = _stage_bucket(state, stage)
        raw = bucket.get("gate")
        if not isinstance(raw, dict) or not raw.get("items"):
            items = parse_template_gate(stage)
            if not items:
                return NO_CHANGE                 # 空 Gate 不得被静默当成通过
            raw = {"items": [{"key": i.key, "label": i.label, "checked": False}
                             for i in items]}
            bucket["gate"] = raw
        for d in raw["items"]:
            if isinstance(d, dict):
                d["checked"] = True
        raw["signed_by"] = by
        raw["signed_at"] = _now()
        signed = True
        return state

    update_state(task, mutate)
    return signed


def reset_gate(task: str, stage: str) -> None:
    """撤销签署（返工时用）。保留 items 定义，只清勾选与签署人。"""
    def mutate(state):
        if not state:
            return NO_CHANGE
        bucket = _stage_bucket(state, stage)
        raw = bucket.get("gate")
        if not (isinstance(raw, dict) and raw.get("items")):
            return NO_CHANGE
        for d in raw["items"]:
            if isinstance(d, dict):
                d["checked"] = False
        raw["signed_by"] = None
        raw["signed_at"] = None
        return state

    update_state(task, mutate)


# ── Route 读写（仅 04-review）──

def _normalize_route(target: str) -> Optional[str]:
    """把各种大小写写法归一到 STAGES 里的标准值。无法识别返回 None。"""
    t = (target or "").strip().strip('`').lower()
    for s in STAGES:
        if s.lower() == t:
            return s
    return None


def read_route(task: str, stage: str = "04-review") -> Optional[str]:
    """读取路由决策。未决返回 None。"""
    bucket = _read_bucket(task, stage)
    raw = bucket.get("route")
    if not isinstance(raw, dict):
        return None
    return _normalize_route(str(raw.get("target") or ""))


def write_route(task: str, target: str, by: str = "user",
                stage: str = "04-review") -> bool:
    """写入路由决策。target 非法阶段名时拒绝写入并返回 False。"""
    norm = _normalize_route(target)
    if norm is None:
        return False
    ok = False

    def mutate(state):
        nonlocal ok
        if not state:
            return NO_CHANGE
        bucket = _stage_bucket(state, stage)
        bucket["route"] = {"target": norm, "decided_by": by,
                           "decided_at": _now()}
        ok = True
        return state

    update_state(task, mutate)
    return ok


def reset_route(task: str, stage: str = "04-review") -> None:
    """作废当前的路由决策，同时把它归档进 ``route_history``。

    「作废」而不是「删除」：Route 是用户的决定，事后复盘要能看出每一轮评审
    分别路由到了哪里。清空后 read_route 返回 None，于是回到 04-review 时
    路由面板重新出现、推进重新等待新决策 —— 这正是修 03↔04 死循环所需的
    效果（任务 T3）；而历史留在状态里，审计不受影响。
    """
    def mutate(state):
        if not state:
            return NO_CHANGE
        bucket = _stage_bucket(state, stage)
        current = bucket.pop("route", None)
        if current is None:
            return NO_CHANGE
        history = bucket.get("route_history")
        if not isinstance(history, list):
            history = []
        history.append(current)
        bucket["route_history"] = history
        return state

    update_state(task, mutate)


def read_route_history(task: str, stage: str = "04-review") -> List[Dict[str, Any]]:
    """历史上已作废的路由决策，按时间先后排列。"""
    bucket = _read_bucket(task, stage)
    raw = bucket.get("route_history")
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


# ── 选项组拍板记录 ──
#
# 用户经 ask_user 做出的选择属于「判定依据」，和 Gate/Route 同级，因此存在
# .state 里。此前它只被拼成字符串回给 agent，校验却要求 agent 把它转写成
# `[x]` / `(Chosen)` / `- **Chosen**:` 之一 —— 于是能不能过闸取决于 agent 的
# 书写习惯，而不是用户是否真的拍了板（任务 T1）。

def read_decisions(task: str, stage: str) -> Dict[str, Any]:
    """读取本阶段的拍板记录，形如 ``{question: {answer, decided_by, decided_at}}``。"""
    bucket = _read_bucket(task, stage)
    raw = bucket.get("decisions")
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def count_decisions(task: str, stage: str) -> int:
    """本阶段已拍板的问题数。"""
    return len(read_decisions(task, stage))


def record_decision(task: str, stage: str, question: str, answer: str,
                    by: str = "user") -> bool:
    """记录一次拍板。以 question 为 key，改主意会覆盖而非追加。

    空答复不记：ask_user 超时或用户直接回车时回落的占位串不代表决定。
    """
    q = (question or "").strip()
    a = (answer or "").strip()
    if not q or not a:
        return False
    ok = False

    def mutate(state):
        nonlocal ok
        if not state:
            return NO_CHANGE
        bucket = _stage_bucket(state, stage)
        decisions = bucket.get("decisions")
        if not isinstance(decisions, dict):
            decisions = {}
        decisions[q] = {"answer": a, "decided_by": by, "decided_at": _now()}
        bucket["decisions"] = decisions
        ok = True
        return state

    update_state(task, mutate)
    return ok


# ── AI Output 边界 nonce ──

def issue_output_nonce(task: str, stage: str) -> str:
    """为本阶段的产出区分配（或复用）一个 nonce。

    agent 无法预测 nonce，因此无法伪造产出区边界 —— 这是 `_save_stage_output`
    不再依赖「找下一个 ## Gate」的前提。同一阶段复用同一个 nonce，
    使得多轮 flush 能精确替换上一次的产出而不是层层追加。
    """
    issued = secrets.token_hex(4)

    def mutate(state):
        nonlocal issued
        if not state:
            return NO_CHANGE
        bucket = _stage_bucket(state, stage)
        nonce = bucket.get("output_nonce")
        if isinstance(nonce, str) and nonce:
            issued = nonce                       # 复用：多轮 flush 精确替换
            return NO_CHANGE
        bucket["output_nonce"] = issued
        return state

    update_state(task, mutate)
    return issued


def read_output_nonce(task: str, stage: str) -> Optional[str]:
    bucket = _read_bucket(task, stage)
    nonce = bucket.get("output_nonce")
    return nonce if isinstance(nonce, str) and nonce else None


# ── AI Output 围栏（机器写的边界，agent 无法伪造）──

AI_OUTPUT_HEADING = "## 🤖 AI Output"
_FENCE_START = "<!-- sw:ai-output:start {nonce} -->"
_FENCE_END = "<!-- sw:ai-output:end {nonce} -->"
_FENCE_START_RE = re.compile(r"<!-- sw:ai-output:start ([0-9a-f]{8,}) -->")


def fence_markers(nonce: str) -> Tuple[str, str]:
    """返回该 nonce 对应的起止标记。"""
    return _FENCE_START.format(nonce=nonce), _FENCE_END.format(nonce=nonce)


def render_output_block(nonce: str, output: str) -> str:
    """把产出包进围栏。围栏行由 sw 写，围栏内是 agent 原文，不做任何解析。"""
    start, end = fence_markers(nonce)
    return f"{AI_OUTPUT_HEADING}\n{start}\n{output.rstrip()}\n{end}\n"


def split_output_region(content: str) -> Optional[Tuple[str, str]]:
    """切出既有产出区，返回 (区前, 区后)；文件里没有产出区则返回 None。

    定位规则只有一条：**第一个** ``start`` 标记，配上带同一 nonce 的
    **最后一个** ``end`` 标记。两端都由 sw 写入，所以规则是自洽的：

    * 取第一个 start —— 真正的围栏由 sw 在 agent 文本之前写下，任何出现在
      产出正文里的 start 一定更靠后。
    * 取最后一个同 nonce 的 end —— agent 有文件读权限，可能把围栏原样抄进
      正文；真正的收尾标记永远是最后那个。nonce 不同的 end（agent 瞎猜的）
      直接忽略。

    这里刻意**不**依赖 ``.state`` 里的 nonce：`.state` 丢失或被清掉时，仍然
    要能认出文件里已有的产出区，否则每轮 flush 都会追加一份。
    """
    m = _FENCE_START_RE.search(content)
    if not m:
        return None
    s = m.start()
    _, end = fence_markers(m.group(1))
    e = content.rfind(end)
    if e < s:
        return None

    before = content[:s]
    # 标题由 render_output_block 一起写，回收时也要一起去掉，避免标题重复堆积
    h = before.rfind(AI_OUTPUT_HEADING)
    if h >= 0 and not before[h + len(AI_OUTPUT_HEADING):].strip():
        before = before[:h]
    return before, content[e + len(end):]


# ── 校验 ──

def stage_todo(task: str, stage: str) -> List[str]:
    """门禁待办。纯 JSON 判定，与 agent 写了什么无关。"""
    gate = read_gate(task, stage)
    todo: List[str] = []
    if not gate.exists:
        todo.append(f"{stage} — 模板未定义 ## Gate 项（请检查 templates/{stage}.md）")
        return todo
    if not gate.signed:
        n = len(gate.pending)
        todo.append(f"{stage} — {n} 个门禁项待签署")
    if stage == "04-review" and read_route(task) is None:
        todo.append("04-review — Route 决策未填写")
    return todo


# ── Markdown 渲染（单向：状态 -> 文件）──

_GATE_RENDER_NOTE = "<!-- 由 sw 渲染，编辑无效；签署状态存于 .state -->"


def render_gate_section(task: str, stage: str) -> bool:
    """把 Gate 状态渲染进 {stage}.md，供人阅读。返回是否改动了文件。

    **单向**：文件是状态的映像，反过来改文件不影响任何判定。因此这里可以
    放心地整段重写 —— 即使 agent 之前在正文里写了 ``## Gate``，重复的区块
    最多是显示噪音。

    渲染目标是**最后一个** ``## Gate`` 区（模板 Gate 位于文件末尾）。找不到
    就在末尾追加一个，这样旧文件也能获得可读的签署视图。
    """
    path = TASKS / task / f"{stage}.md"
    if not path.exists():
        return False
    gate = read_gate(task, stage)
    if not gate.exists:
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    lines = [f"## Gate", _GATE_RENDER_NOTE]
    for item in gate.items:
        mark = "x" if item.checked else " "
        lines.append(f"- [{mark}] {item.label}")
    block = "\n".join(lines) + "\n"

    pos = content.rfind("\n## Gate")
    if pos < 0:
        new_content = content.rstrip("\n") + "\n\n" + block
    else:
        # Gate 区一直延伸到下一个二级标题（通常就是文件末尾）
        tail = content[pos + 1:]
        nl = tail.find("\n## ", 1)
        after = tail[nl + 1:] if nl >= 0 else ""
        new_content = content[:pos + 1] + block + (("\n" + after) if after else "")

    if new_content == content:
        return False
    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError:
        return False
    return True
