from sw_lib.agents.pty import PTYProcessor

def test_csi_sequences():
    processor = PTYProcessor()
    raw = b"\x1b[31mhello\x1b[0m \x1b[1;32mOK\x1b[0m\n"
    lines = processor.process_chunk(raw)
    assert lines == ["hello OK"]

def test_carriage_return():
    processor = PTYProcessor()
    text = b"line1\rline2\n"
    lines = processor.process_chunk(text)
    assert lines == ["line2"]
