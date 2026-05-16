from sw_lib.ui.tui import extract_options, detect_input_mode

def test_extract_options():
    lines = [("agent", "1. 选项一"), ("agent", "2. 选项二")]
    opts = extract_options(lines)
    assert len(opts) >= 2

def test_detect_input_mode_yesno():
    lines = [("agent", "Do you approve this design?")]
    mode = detect_input_mode(lines, [])
    assert mode == "yesno"
