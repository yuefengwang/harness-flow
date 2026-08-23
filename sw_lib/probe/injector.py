"""sw_lib.probe.injector — 变异注入器（B0 实现的最小子集）。

设计依据：docs/design/A11-mutation-probe.md 的 3.1 / 2.3，由 B0 的 3.3 授权先落。
**A11 后续应接管本文件并扩充算子**，不要并存两份实现（B0 第 10 节）。

⚠️ **头号实现陷阱：不得用 `ast.unparse` 回写**（A11 的 2.3，已实测）。

那条路看起来最自然 —— AST 改完直接 unparse。但实测同一个 `<` → `<=`
变异，unparse 回写产生 **3 增 4 删**：注释被删掉、双引号变单引号、
末尾换行丢失。后果不是「不好看」，而是探针失效：

- 注释被删 → 设计审视者会去评论那些噪音，而不是那个缺陷；
- diff 被噪音淹没 → 测的变成「能否在噪音里发现缺陷」；
- numstat 失真 → A3 的风险排序错乱。

**定案**：AST 只用来**定位**（lineno / col_offset / end_col_offset），
替换在原始文本的字节区间上做，其余字节一律不动。实测 diff 为 1 增 1 删。

> ⚠️ 本文件当前是**骨架**（DEV-PROTOCOL 步 1）：入口一律返回空值。
"""

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 基线可用的算子。M1 刻意**不在此表**：A11 的 2.1 实测它会被现有测试抓住，
# 那测的是测试套件而不是 reviewer，放进基线会让分母虚高（B0 的验收 8）。
BASELINE_OPERATORS: Tuple[str, ...] = ("M2", "M4")

# M2：比较运算符边界偏移。成对定义，方向两边都要能走。
_M2_SWAP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}

_M2_AST_OPS = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
}

# M4：布尔取反。`and` ↔ `or`。
_M4_SWAP = {"and": "or", "or": "and"}

_M4_AST_OPS = {ast.And: "and", ast.Or: "or"}


def find_sites(source: str, operator: str = "M2") -> List[Dict[str, Any]]:
    """定位可注入的位点，返回原始文本里的**字节区间**。

    AST 只用来定位，不参与回写（A11 的 2.3）。给出字节区间是必须的：
    只给行号时，一行里出现多个运算符就无法精准替换。

    M1 一律返回空 —— 它不是基线样本（B0 的验收 8）。
    """
    if operator not in BASELINE_OPERATORS:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines(keepends=True)
    # 行首在整份文本中的绝对偏移，用于把 (lineno, col) 换算成字节位置。
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    sites: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if operator == "M2":
            sites.extend(_m2_sites(node, source, offsets))
        elif operator == "M4":
            sites.extend(_m4_sites(node, source, offsets))

    sites.sort(key=lambda s: s["start"])
    return sites


def _abs_pos(offsets: List[int], lineno: int, col: int) -> int:
    """(lineno, col_offset) → 整份文本的绝对偏移。

    注意 `col_offset` 是**字节**偏移（utf-8），而我们在 str 上切片。
    因此换算时必须按字节走，非 ASCII 注释才不会让位置漂掉。
    """
    return offsets[lineno - 1] + col


def _m2_sites(node: ast.AST, source: str,
              offsets: List[int]) -> List[Dict[str, Any]]:
    """比较运算符位点。

    在 `left` 结束与右操作数开始之间的那段文本里找运算符 ——
    直接搜索整行会误中注释里的 `<`。
    """
    if not isinstance(node, ast.Compare):
        return []
    out: List[Dict[str, Any]] = []
    left = node.left
    for op, right in zip(node.ops, node.comparators):
        symbol = _M2_AST_OPS.get(type(op))
        left_end, right_start = _gap_of(left, right, offsets)
        left = right
        if symbol is None or left_end is None:
            continue
        found = _find_symbol(source, left_end, right_start, symbol)
        if found is None:
            continue
        out.append({
            "operator": "M2",
            "start": found,
            "end": found + len(symbol),
            "original": symbol,
            "mutated": _M2_SWAP[symbol],
            "lineno": getattr(node, "lineno", None),
        })
    return out


def _m4_sites(node: ast.AST, source: str,
              offsets: List[int]) -> List[Dict[str, Any]]:
    """布尔运算符位点（`and` / `or`）。"""
    if not isinstance(node, ast.BoolOp):
        return []
    symbol = _M4_AST_OPS.get(type(node.op))
    if symbol is None:
        return []
    out: List[Dict[str, Any]] = []
    for first, second in zip(node.values, node.values[1:]):
        left_end, right_start = _gap_of(first, second, offsets)
        if left_end is None:
            continue
        found = _find_symbol(source, left_end, right_start, symbol)
        if found is None:
            continue
        out.append({
            "operator": "M4",
            "start": found,
            "end": found + len(symbol),
            "original": symbol,
            "mutated": _M4_SWAP[symbol],
            "lineno": getattr(node, "lineno", None),
        })
    return out


def _gap_of(left: ast.AST, right: ast.AST,
            offsets: List[int]) -> Tuple[Optional[int], Optional[int]]:
    """两个操作数之间的文本区间 —— 运算符必然落在这里面。"""
    try:
        left_end = _abs_pos(offsets, left.end_lineno, left.end_col_offset)
        right_start = _abs_pos(offsets, right.lineno, right.col_offset)
    except (AttributeError, TypeError, IndexError):
        return None, None
    return left_end, right_start


def _find_symbol(source: str, start: Optional[int], end: Optional[int],
                 symbol: str) -> Optional[int]:
    """在区间内找运算符。

    `<=` 必须优先于 `<` 匹配，否则 `<=` 会被当成 `<` 而把 `=` 留在后面，
    产出 `<==` 这种语法坏的变异。区间内的 `<` 若紧跟 `=`，跳过。
    """
    if start is None or end is None or start >= end:
        return None
    segment = source[start:end]
    idx = 0
    while True:
        found = segment.find(symbol, idx)
        if found < 0:
            return None
        after = found + len(symbol)
        # 单字符运算符后紧跟 `=` 时，真正的运算符是两字符版本，不是这个。
        if len(symbol) == 1 and symbol in "<>" and \
                segment[after:after + 1] == "=":
            idx = after
            continue
        return start + found


def inject(path, site: Dict[str, Any]) -> Dict[str, Any]:
    """把变异写进文件，返回可用于 `revert` 的备份。

    备份是**完整原文**而不是反向 patch：反向 patch 二次执行会把变异
    再改回去（B0 的 9.3），而 revert 必须幂等。
    """
    target = Path(str(path))
    original = target.read_bytes()
    source = original.decode("utf-8")

    start, end = int(site["start"]), int(site["end"])
    mutated_source = source[:start] + str(site["mutated"]) + source[end:]
    target.write_text(mutated_source, encoding="utf-8")

    # 语法是否被破坏要**如实报告**，不是盲目落盘。语法坏掉的变异会被
    # 判成 collection error 而非真缺陷，混进基线就成了假样本。
    try:
        ast.parse(mutated_source)
        syntax_ok = True
    except SyntaxError:
        syntax_ok = False

    return {
        "path": str(target),
        "original_bytes": original,
        "site": dict(site),
        "syntax_ok": syntax_ok,
    }


def revert(backup: Dict[str, Any]) -> bool:
    """按备份逐字节恢复。幂等 —— 连续调用结果相同。"""
    try:
        path = backup["path"]
        data = backup["original_bytes"]
    except (KeyError, TypeError):
        return False
    try:
        Path(str(path)).write_bytes(data)
        return True
    except OSError:
        return False
