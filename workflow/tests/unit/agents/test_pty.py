import re
from sw_lib.agents.pty import PTYProcessor

def test_csi_sequences():
    processor = PTYProcessor()
    raw = b"\x1b[31mhello\x1b[0m \x1b[1;32mOK\x1b[0m\n"
    lines = processor.process_chunk(raw)
    assert lines == ["hello OK"]

def test_normal_text_preserved():
    processor = PTYProcessor()
    text = b"hello world 123\n"
    lines = processor.process_chunk(text)
    assert lines == ["hello world 123"]

def test_osc_sequences():
    processor = PTYProcessor()
    # \x07 是 OSC 的典型终止符
    text = b"\x1b]0;title\x07normal text\n"
    lines = processor.process_chunk(text)
    assert lines == ["normal text"]

def test_carriage_return():
    processor = PTYProcessor()
    # \r 应该覆盖行首内容
    text = b"line1\rline2\n"
    lines = processor.process_chunk(text)
    assert lines == ["line2"]

def test_backspace():
    processor = PTYProcessor()
    # \b 应该物理删除前一个字符
    text = b"abc\b\bd\n"
    lines = processor.process_chunk(text)
    assert lines == ["ad"]

def test_partial_line_buffering():
    processor = PTYProcessor()
    # 第一次没换行，不应返回行
    res1 = processor.process_chunk(b"hello ")
    assert res1 == []
    # 第二次换行，返回完整行
    res2 = processor.process_chunk(b"world\n")
    assert res2 == ["hello world"]
