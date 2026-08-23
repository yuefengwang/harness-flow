"""选项编号重复渲染成 "A. A. xxx"（任务 T3）。

opencode 的 question options 是 ``{label, description}``，harness 需要 "A." 前缀
才能让 TUI 切出可输入的标号。问题在于模型经常自己就把编号写进 label，
于是无条件补前缀就成了 "[1] A. A. 仅中国A股交易日"。

这里同时钉住反向要求：不能因为怕重复就乱剥 —— 错位编号要原样保留（那说明
模型给的编号本身有问题），"Apple 方案" 这类以字母开头的正常文本更不能动。
"""
import pytest

from sw_lib.agents.opencode import OpenCodeAgent, _strip_leading_label


def _opts(*pairs):
    return {"question": "选哪个？",
            "options": [{"label": l, "description": d} for l, d in pairs]}


def test_model_written_prefix_is_not_duplicated():
    """label 自带 "A." 时不能再叠一层。"""
    norm, mapping = OpenCodeAgent._normalize_question(
        _opts(("A. 仅中国A股交易日", "只考虑A股"), ("B. 含美股", "支持美股")))

    assert norm["options"] == ["A. 仅中国A股交易日 - 只考虑A股",
                              "B. 含美股 - 支持美股"]
    # 映射必须仍指向 opencode 认的原始 label，否则回答 POST 回去服务端不认。
    assert mapping["A. 仅中国A股交易日 - 只考虑A股"] == "A. 仅中国A股交易日"


def test_plain_label_still_gets_prefix():
    """label 不带编号时（多数模型如此）照旧补前缀。"""
    norm, _ = OpenCodeAgent._normalize_question(
        _opts(("Python (推荐)", "标准库好"), ("Go", "性能好")))

    assert norm["options"] == ["A. Python (推荐) - 标准库好", "B. Go - 性能好"]


def test_options_without_description():
    norm, _ = OpenCodeAgent._normalize_question(_opts(("方案甲", ""), ("方案乙", "")))

    assert norm["options"] == ["A. 方案甲", "B. 方案乙"]


@pytest.mark.parametrize("label,prefix,expected", [
    ("A. 仅中国A股交易日", "A", "仅中国A股交易日"),
    ("A、中文顿号", "A", "中文顿号"),
    ("A) 圆括号", "A", "圆括号"),
    ("A: 冒号", "A", "冒号"),
    ("A： 全角冒号", "A", "全角冒号"),
    ("a. 小写编号", "A", "小写编号"),
    # 错位编号原样保留：模型把第 0 项标成 B 是它自己的 bug，掩盖了更难查。
    ("B. 错位了", "A", "B. 错位了"),
    # 剥完为空说明 label 只有一个编号，剥掉就没内容可显示了。
    ("A.", "A", "A."),
    # 以编号字母开头的正常词不能误伤。
    ("Apple 方案", "A", "Apple 方案"),
    ("A股相关", "A", "A股相关"),
    # 没有分隔符不算编号。
    ("A 空格开头", "A", "A 空格开头"),
])
def test_strip_leading_label_matrix(label, prefix, expected):
    assert _strip_leading_label(label, prefix) == expected


def test_prefix_beyond_z_uses_number():
    """超过 26 项时前缀退化成数字，剥离逻辑不能崩。"""
    pairs = [(f"选项{i}", "") for i in range(27)]
    norm, _ = OpenCodeAgent._normalize_question(_opts(*pairs))

    assert norm["options"][26] == "27. 选项26"
