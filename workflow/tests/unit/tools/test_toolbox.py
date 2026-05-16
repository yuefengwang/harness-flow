from sw_lib.core.config import get_tools_for_stage
from sw_lib.tools.toolbox import Toolbox

def test_brainstorming_tools_readonly():
    tools = get_tools_for_stage("01-brainstorming")
    assert "list_files" in tools
    assert "read_file" in tools
    assert "write_file" not in tools
    assert "run_command" not in tools

def test_coding_tools_full():
    tools = get_tools_for_stage("03-coding")
    assert "list_files" in tools
    assert "read_file" in tools
    assert "write_file" in tools
    assert "run_command" in tools

def test_toolbox_get_available_tools():
    tb = Toolbox("test-task", "01-brainstorming")
    tools = tb.get_available_tools()
    assert len(tools) >= 2
    tool_names = [t.__name__ for t in tools]
    assert "list_files" in tool_names
    assert "read_file" in tool_names
    assert "write_file" not in tool_names

def test_toolbox_coding_stage():
    tb = Toolbox("test-task", "03-coding")
    tools = tb.get_available_tools()
    tool_names = [t.__name__ for t in tools]
    assert "write_file" in tool_names
    assert "run_command" in tool_names
