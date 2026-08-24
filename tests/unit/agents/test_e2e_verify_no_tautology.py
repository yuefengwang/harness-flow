"""e2e 验收脚本里不许有恒真的检查项。

现场：本轮把 mock 的 02 产出补厚之后，e2e 打出这一行

    [✓] WBS items preserved (unchecked OK) — 0 unchecked WBS items (expected)

数出来是 **0** 条 —— 而 mock 的产出里明明有 3 条 `1. [ ] Task 1` 形态的 WBS
条目。两处对不上，说明这条判据根本没在判它自称要判的东西。查下去是两个 bug
叠在一起：

1. `ok` 参数写死成字面量 `True` —— 无论数出几条都记 `[✓]`。它在 44 项里
   白占一格，制造「覆盖到了」的错觉。
2. 取产出区用 `sf02.split("## 🤖 AI Output")[1]`。产出区由
   `render_output_block` 写成「标题 + 围栏 + agent 原文」，而 agent 原文里
   自己也带一个 `## 🤖 AI Output` 标题（mock 与真实 agent 都会写）。于是
   split 出 3 段，`[1]` 只是夹在两个标题之间的那行围栏注释，正文全在 `[2]`。

这与本轮修掉的 `grep -q "## Task DAG"`（标题是模板自带的，判据恒真）是同一
类失效：**判据存在、结论恒定、没人发现**。累计第五次撞上「单元全绿 ≠ 机制
接通」，所以这次用测试钉住形态，而不是只把那一行改对。

判据故意做成源码级扫描而不是「跑一遍 e2e 看结果」：恒真判据的症状恰恰是
「跑起来永远是绿的」，用运行结果验证它等于用它自己作证。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERIFY = ROOT / "tests" / "e2e-flow" / "verify.py"


def _check_calls(src: str):
    """切出所有 `self.check(...)` 调用的实参列表。

    手工配对括号而不是用正则：判据里含 `f"..."`、嵌套调用与括号，
    正则切出来的参数边界不可靠，而这个测试的全部价值就在于边界切得准。
    """
    calls = []
    for m in re.finditer(r"self\.check\(", src):
        depth, i = 1, m.end()
        while depth and i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        body = src[m.end():i - 1]

        args, d, cur = [], 0, ""
        for ch in body:
            if ch in "([{":
                d += 1
            elif ch in ")]}":
                d -= 1
            if ch == "," and d == 0:
                args.append(cur.strip())
                cur = ""
            else:
                cur += ch
        args.append(cur.strip())
        calls.append((src[:m.start()].count("\n") + 1, args))
    return calls


def test_verify_script_has_check_calls():
    """前提自检：真的扫到了检查项。

    若解析器切不出任何调用，下面那条断言会「通过」—— 空循环假绿。
    """
    calls = _check_calls(VERIFY.read_text(encoding="utf-8"))
    assert len(calls) >= 10, f"只解析出 {len(calls)} 个 self.check 调用，解析器失效"


def test_no_check_is_hardcoded_true():
    """没有任何检查项把结论写死成 `True`。

    写死 `False` 是允许的：那是「已经确定不通过」的分支（如 facts/ 不存在），
    结论由控制流决定，仍是真判断。写死 `True` 没有这种解释 ——
    它只会永远报绿。
    """
    src = VERIFY.read_text(encoding="utf-8")
    tautologies = [(line, args[1]) for line, args in _check_calls(src)
                   if len(args) >= 3 and args[2] == "True"]

    assert not tautologies, (
        "这些验收项恒为真，永远不会失败:\n"
        + "\n".join(f"  verify.py:{ln}  {item}" for ln, item in tautologies))


def test_output_region_is_located_by_fence_not_heading_split():
    """取产出区要按 sw 写的围栏，不能 split 标题。

    `## 🤖 AI Output` 在文件里会出现两次（sw 写的那个 + agent 自己在正文里
    写的那个），按它 split 拿到的段落是错的。围栏标记带 nonce、agent 猜不到，
    是唯一可靠的边界 —— `stage_state.split_output_region` 已经实现了这条规则。
    """
    src = VERIFY.read_text(encoding="utf-8")
    assert 'split("## 🤖 AI Output")' not in src, \
        "仍在按标题 split 产出区：agent 正文里的同名标题会让切分错位"
    assert "sw:ai-output" in src, \
        "verify.py 没有按围栏标记定位产出区"
