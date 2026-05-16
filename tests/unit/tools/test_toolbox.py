from sw_lib.core.config import get_tools_for_stage
from sw_lib.tools.toolbox import Toolbox

def test_brainstorming_tools_readonly():
    tools = get_tools_for_stage("01-brainstorming")
    assert "list_files" in tools
    assert "read_file" in tools

def test_toolbox_get_available_tools():
    tb = Toolbox("test-task", "01-brainstorming")
    tools = tb.get_available_tools()
    assert len(tools) >= 2
