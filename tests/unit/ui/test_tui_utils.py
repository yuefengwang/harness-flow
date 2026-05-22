from sw_lib.ui.tui import extract_options, detect_input_mode

# ── extract_options ──

def test_extract_options_with_keyword():
    """选项标签中包含关键词"选项" — 应正常提取"""
    lines = [("agent", "1. 选项一"), ("agent", "2. 选项二")]
    opts = extract_options(lines)
    assert len(opts) >= 2

def test_plain_numbered_list_not_extracted():
    """纯编号列表（无提问上下文）— 不应被误提取为选项"""
    lines = [("agent", "1. install deps"), ("agent", "2. configure env"), ("agent", "3. run tests")]
    opts = extract_options(lines)
    assert opts == [], f"plain numbered list should NOT extract options, got {opts}"

def test_numbered_list_with_question_context():
    """带问号上下文的编号列表 — 应正常提取"""
    lines = [("agent", "Which approach?"), ("agent", "1. approach A"), ("agent", "2. approach B")]
    opts = extract_options(lines)
    assert len(opts) == 2

def test_chinese_question_context():
    """中文"请选择"上下文 — 应正常提取"""
    lines = [("agent", "请选择方案："), ("agent", "1. 方案A"), ("agent", "2. 方案B")]
    opts = extract_options(lines)
    assert len(opts) == 2

def test_keyword_choose_context():
    """英文 choose 关键词 — 应正常提取"""
    lines = [("agent", "Choose one:"), ("agent", "1. Option A"), ("agent", "2. Option B")]
    opts = extract_options(lines)
    assert len(opts) == 2

def test_option_keyword_context():
    """option 关键词 — 应正常提取"""
    lines = [("agent", "Available options:"), ("agent", "1. first"), ("agent", "2. second")]
    opts = extract_options(lines)
    assert len(opts) == 2

def test_letter_labels_with_question():
    """字母标签 + 问号 — 应正常提取"""
    lines = [("agent", "Which do you prefer?"), ("agent", "A. option one"), ("agent", "B. option two")]
    opts = extract_options(lines)
    assert len(opts) == 2

def test_descriptive_numbered_list_with_incidental_question():
    """描述性编号列表末尾有问句 — 不应提取为选项 (修复1.xxxx 2.xxxx 3.xxxx误判)"""
    # Simulates: agent returned situation description "1.xxx. 2.xxx. 3.xxx."
    # with an incidental question mark at the very end of a different message
    lines = [
        ("agent", "Here is the current status:"),
        ("agent", "1. project created successfully."),
        ("agent", "2. dependencies installed."),
        ("agent", "3. configuration completed."),
        ("agent", "Does this look correct?"),  # ? far from numbered items
    ]
    opts = extract_options(lines)
    assert opts == [], f"descriptive list with distant ? should NOT extract, got {opts}"

def test_descriptive_chinese_list_with_incidental_question():
    """中文描述性编号列表末尾有问句 — 不应提取为选项"""
    lines = [
        ("agent", "当前情况说明："),
        ("agent", "1. 项目目录已创建完成。"),
        ("agent", "2. 依赖环境已安装。"),
        ("agent", "3. 配置文件已生成。"),
        ("agent", "您觉得这样可以吗？"),  # ？在单独一行，远离编号项
    ]
    opts = extract_options(lines)
    assert opts == [], f"Chinese descriptive list with distant ？ should NOT extract, got {opts}"

def test_keyword_in_same_line_as_option():
    """弱信号关键词与选项在同一行 — 应正常提取"""
    lines = [("agent", "1. 选项一"), ("agent", "2. 选项二")]
    opts = extract_options(lines)
    assert len(opts) == 2

def test_keyword_adjacent_to_option():
    """弱信号关键词在选项的上一行 — 应正常提取"""
    lines = [("agent", "请从下面选择："), ("agent", "1. 方案A"), ("agent", "2. 方案B")]
    opts = extract_options(lines)
    assert len(opts) == 2

def test_question_mark_in_single_message_with_numbered_list_far():
    """同一消息中问号远离编号列表 — 不应提取"""
    lines = [("agent", "1. Step one completed.\n2. Step two done.\n3. Step three finished.\n\nShould I continue?")]
    opts = extract_options(lines)
    assert opts == [], f"message with distant ? should NOT extract, got {opts}"

# ── detect_input_mode ──

def test_detect_input_mode_yesno():
    lines = [("agent", "Do you approve this design?")]
    mode = detect_input_mode(lines, [])
    assert mode == "yesno"

def test_detect_mode_none_with_plain_list():
    """纯编号列表不应触发 options 模式"""
    plain_lines = [("agent", "Here are steps:"), ("agent", "1. first"), ("agent", "2. second")]
    mode = detect_input_mode(plain_lines, [])
    assert mode == "none", f"expected none, got {mode}"

def test_detect_mode_none_when_user_last():
    """最后一条消息来自 user 时不应进入选项模式"""
    lines = [("agent", "Which one?"), ("agent", "1. A"), ("agent", "2. B"), ("user", "A")]
    mode = detect_input_mode(lines, [("1", "A"), ("2", "B")])
    assert mode == "none", f"expected none after user reply, got {mode}"
