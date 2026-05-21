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
