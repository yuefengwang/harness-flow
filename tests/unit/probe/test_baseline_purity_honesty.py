"""B0 的纯度声明必须随实际改造进展更新。

`check_review_side_clean()` 原判据只看 A6-A9 四个模块文件是否存在。
但 A4（多角色配置）与 A5（04 展开为并行子图 + 异构模型）**已经改了审查侧**：
04-review 不再是单 reviewer，审查者数量与模型都变了。

于是出现一个不诚实的状态：判据报 `clean: true`、纯度声明写着
「采集时 04-review 仍是单 reviewer 角色」，而实际早已不是。
A11 若引用这份基线做对照，会把 A4/A5 带来的变化算进 A6-A9 的功劳。

判据：污染维度必须包含所有已落盘的审查侧改造，且声明文字不得与事实矛盾。
"""

from sw_lib.probe import baseline as B


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


def test_untouched_still_lists_unimplemented_tasks():
    """未实施的任务仍须如实列出，供 A11 判断可比性。"""
    untouched = B.check_review_side_clean()["purity_detail"]["review_side_untouched"]
    for t in ("A6", "A7", "A8", "A9"):
        assert t in untouched, f"{t} 尚未实施却未列入 untouched"
