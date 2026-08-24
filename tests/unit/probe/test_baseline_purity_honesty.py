"""B0 的纯度声明必须随实际改造进展更新。

`check_review_side_clean()` 原判据只看 A6-A9 四个模块文件是否存在。
但 A4（多角色配置）与 A5（04 展开为并行子图 + 异构模型）**已经改了审查侧**：
04-review 不再是单 reviewer，审查者数量与模型都变了。

于是出现一个不诚实的状态：判据报 `clean: true`、纯度声明写着
「采集时 04-review 仍是单 reviewer 角色」，而实际早已不是。
A11 若引用这份基线做对照，会把 A4/A5 带来的变化算进 A6-A9 的功劳。

判据：污染维度必须包含所有已落盘的审查侧改造，且声明文字不得与事实矛盾。
"""

from pathlib import Path

from sw_lib.probe import baseline as B

ROOT = Path(__file__).resolve().parents[3]


def test_a5_subgraph_counts_as_review_side_contamination():
    """A5 已落盘（review_graph.py 存在），必须被记为污染维度。"""
    r = B.check_review_side_clean()
    assert "A5" in r["contaminated_dimensions"], (
        f"A5 已落盘却未被记为污染：{r['contaminated_dimensions']} —— "
        "基线会把 A5 的效果算进 A6-A9")


def test_a4_multi_role_counts_as_contamination():
    r = B.check_review_side_clean()
    assert "A4" in r["contaminated_dimensions"]


def test_not_clean_when_contaminated():
    r = B.check_review_side_clean()
    assert r["clean"] is False
    assert r["baseline_purity"] == "partially_contaminated"


def test_purity_note_does_not_claim_single_reviewer():
    """声明文字不得再说「仍是单 reviewer 角色」—— 那与事实矛盾。"""
    note = B.check_review_side_clean()["purity_detail"]["note"]
    assert "仍是单 reviewer" not in note, \
        "纯度声明仍称 04 是单 reviewer，而 A5 已把它展开为并行子图"


def test_untouched_matches_actual_file_absence():
    """untouched 必须与**实际文件存在性**一致，不能是一份写死的名单。

    ⚠️ 本测试按 DEV-PROTOCOL 1.2 **显式声明重做**。
    原判据是 `for t in ("A6","A7","A8","A9"): assert t in untouched`。
    A6 落地后 `objective_check.py` 存在，纯度检查如实把 A6 移出 untouched ——
    那正是它该做的事。继续断言 A6 未实施，等于要求纯度检查对已落盘的改造
    装作没看见，而那恰是「诚实纯度」这项改动要消除的行为。

    新判据对任何维度的落地都成立，不必随 A7/A8/A9 逐个落地反复改。
    """
    result = B.check_review_side_clean()
    detail = result["purity_detail"]
    untouched = set(detail["review_side_untouched"])
    contaminated = set(result.get("contaminated_dimensions") or [])

    for rel in B.REVIEW_SIDE_MODULES:
        task = B._MODULE_TO_TASK.get(Path(rel).name)
        if not task:
            continue
        exists = (ROOT / rel).is_file()
        if exists:
            assert task not in untouched, (
                f"{rel} 已存在，{task} 却仍被列为 untouched —— 基线在说谎")
            assert task in contaminated, (
                f"{rel} 已存在，{task} 却未列入 contaminated_dimensions")
        else:
            assert task in untouched, (
                f"{rel} 不存在，{task} 却未列入 untouched")


def test_untouched_and_contaminated_do_not_overlap():
    """同一维度不得既算未触碰又算已污染。"""
    result = B.check_review_side_clean()
    untouched = set(result["purity_detail"]["review_side_untouched"])
    contaminated = set(result.get("contaminated_dimensions") or [])
    assert not (untouched & contaminated), (
        f"维度分类自相矛盾：{untouched & contaminated}")
