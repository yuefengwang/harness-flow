from sw_lib.ui.tui import extract_options, detect_input_mode

def test_extract_options():
    lines = [
        ("agent", "1. 选项一"),
        ("agent", "2. 选项二"),
        ("agent", "- 选项 A: 方案A"),
    ]
    opts = extract_options(lines)
    assert len(opts) >= 3
    assert opts[0][0] == "1"
    assert opts[2][0] == "A"

def test_detect_input_mode_yesno():
    lines = [("agent", "Do you approve this design?")]
    mode = detect_input_mode(lines, [])
    assert mode == "yesno"

def test_detect_input_mode_options():
    lines = [("agent", "请选择：")]
    options = [("1", "选项一")]
    mode = detect_input_mode(lines, options)
    assert mode == "options"

def test_detect_input_mode_none():
    lines = [("agent", "这是一段普通对话")]
    mode = detect_input_mode(lines, [])
    assert mode == "none"
