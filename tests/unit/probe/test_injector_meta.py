"""注入器元测试（A11 的 3.5，由 B0 的 9.1 要求先落）。

**未经元测试的注入器不得用于基线采集。** 理由是本任务特有的：
采集脚本若有 bug，会把「探针没生效」记成「reviewer 没反应」——
而 A11 的数据可以重采，B0 的不能。

最要紧的两条是 `test_m2_diff_is_one_line_swap`（注入规模受控）
与 `test_revert_restores_byte_for_byte`（工作树恢复）：
它们分别对应 A11 的 2.3 与 B0 的验收 3，都是实测踩出来的真实陷阱。
"""

import ast
import subprocess
from pathlib import Path

import pytest

from sw_lib.probe import injector as inj

# 含注释、docstring、双引号的样本 —— 这三样正是 ast.unparse 会破坏的东西。
SAMPLE = '''"""Token 校验。"""


def verify_token(token, now):
    """过期即拒绝。"""
    # 这个阈值来自业务规格 SPEC-3
    if token["exp"] < now:
        return False
    return True
'''


def _write(tmp_path, body=SAMPLE, name="auth.py"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _git_init(tmp_path):
    """建一个干净的 git 仓库 —— 注入只允许发生在干净仓库上（B0 的 9.2）。"""
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=b0@probe", "-c", "user.name=b0",
                  "commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=str(tmp_path),
                       capture_output=True, check=True)


def _numstat(tmp_path):
    out = subprocess.run(["git", "diff", "--numstat"], cwd=str(tmp_path),
                         capture_output=True, text=True).stdout.strip()
    if not out:
        return (0, 0)
    added, removed = out.split()[:2]
    return (int(added), int(removed))


# ── 定位 ──

def test_find_sites_locates_comparison_operator():
    """M2 要能找到比较运算符的位置。"""
    sites = inj.find_sites(SAMPLE, "M2")

    assert sites, "M2 在含 `<` 的样本里找不到任何位置"
    assert any(s.get("original") == "<" for s in sites), sites


def test_find_sites_reports_byte_offsets_not_just_lines():
    """必须给出**字节区间**，文本级精准替换才做得到（A11 的 2.3）。

    只给行号的话，一行里有多个运算符时无法精准替换。
    """
    site = inj.find_sites(SAMPLE, "M2")[0]

    for key in ("start", "end", "original", "mutated"):
        assert key in site, f"site 缺 {key}: {site}"
    assert SAMPLE[site["start"]:site["end"]] == site["original"], \
        "字节区间与 original 不一致 —— 替换会错位"


def test_m1_is_not_offered_for_baseline():
    """M1 不进入基线样本（验收 8）。

    A11 的 2.1 实测 M1 会被现有测试抓住 —— 那测的是测试套件，
    不是 reviewer。放进基线会让分母虚高、检出率虚假。
    """
    assert inj.find_sites(SAMPLE, "M1") == [], \
        "M1 竟然给出了基线样本位点"


# ── 注入 ──

def test_inject_actually_writes_the_mutation(tmp_path):
    """变异确实落盘（元测试第一条：探针真的生效了）。"""
    p = _write(tmp_path)
    site = inj.find_sites(SAMPLE, "M2")[0]

    inj.inject(p, site)

    assert p.read_text(encoding="utf-8") != SAMPLE, "文件没被改动"
    assert "<=" in p.read_text(encoding="utf-8")


def test_inject_preserves_comments_and_quotes(tmp_path):
    """注释与双引号逐字保留 —— 这是不用 ast.unparse 的全部理由。"""
    p = _write(tmp_path)
    inj.inject(p, inj.find_sites(SAMPLE, "M2")[0])
    out = p.read_text(encoding="utf-8")

    assert "SPEC-3" in out, "注释被删了 —— 说明走了 ast.unparse 那条路"
    assert '"""Token 校验。"""' in out, "docstring 的双引号被改写了"
    assert out.endswith("\n"), "末尾换行丢了"


def test_m2_diff_is_one_line_swap(tmp_path):
    """**验收 9**：M2 的 diff 必须是 1 增 1 删。

    这是 A11 的 2.3 的回归锚点。用 ast.unparse 时实测为 3 增 4 删，
    那些多出来的行是格式抖动 —— 会把「能否发现缺陷」变成
    「能否在噪音里发现缺陷」。
    """
    p = _write(tmp_path)
    _git_init(tmp_path)
    inj.inject(p, inj.find_sites(SAMPLE, "M2")[0])

    assert _numstat(tmp_path) == (1, 1), (
        f"注入规模失控: {_numstat(tmp_path)}，期望 (1, 1)")


def test_inject_keeps_syntax_valid(tmp_path):
    """语法不能被破坏，否则变异会被判成 collection error 而非真缺陷。"""
    p = _write(tmp_path)
    result = inj.inject(p, inj.find_sites(SAMPLE, "M2")[0])

    ast.parse(p.read_text(encoding="utf-8"))
    assert result.get("syntax_ok") is True, result


def test_syntax_breaking_mutation_is_flagged(tmp_path):
    """故意造一个语法坏的变异应被判 `syntax_ok: False`（A11 的 3.5）。

    这条测的是注入器**认得出**自己搞坏了，而不是盲目落盘。
    """
    p = _write(tmp_path)
    bad = {"start": SAMPLE.index("if"), "end": SAMPLE.index("if") + 2,
           "original": "if", "mutated": "if if if", "operator": "M2"}

    result = inj.inject(p, bad)

    assert result.get("syntax_ok") is False, \
        "把语法搞坏了却报 syntax_ok=True"


# ── 撤销 ──

def test_revert_restores_byte_for_byte(tmp_path):
    """**验收 3**：revert 后逐字节等于原文。

    必须恢复备份而不是打反向 patch —— 后者二次执行会把变异又改回去。
    """
    p = _write(tmp_path)
    backup = inj.inject(p, inj.find_sites(SAMPLE, "M2")[0])
    assert p.read_text(encoding="utf-8") != SAMPLE, "前提不成立：没注入"

    assert inj.revert(backup) is True
    assert p.read_bytes() == SAMPLE.encode("utf-8"), "revert 未逐字节恢复"


def test_revert_is_idempotent(tmp_path):
    """连续 revert 两次仍等于原文（A11 的 3.5 最后一条）。"""
    p = _write(tmp_path)
    backup = inj.inject(p, inj.find_sites(SAMPLE, "M2")[0])

    inj.revert(backup)
    inj.revert(backup)

    assert p.read_bytes() == SAMPLE.encode("utf-8"), "第二次 revert 破坏了原文"


def test_working_tree_is_clean_after_inject_revert_cycle(tmp_path):
    """**验收 3 的端到端形态**：注入 → revert 之后 git 应当无差异。"""
    p = _write(tmp_path)
    _git_init(tmp_path)
    backup = inj.inject(p, inj.find_sites(SAMPLE, "M2")[0])
    inj.revert(backup)

    porcelain = subprocess.run(["git", "status", "--porcelain"],
                               cwd=str(tmp_path), capture_output=True,
                               text=True).stdout.strip()
    assert porcelain == "", f"工作树未恢复干净: {porcelain}"
