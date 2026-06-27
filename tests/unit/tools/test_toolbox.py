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

def test_read_progress_tool_execution(dummy_task):
    from sw_lib.core.config import TASKS
    from sw_lib.tools.toolbox import ReadProgressTool
    task_dir = TASKS / dummy_task
    progress_file = task_dir / "progress.txt"
    progress_file.write_text("Handover check: progress verified.", encoding="utf-8")

    tool = ReadProgressTool(dummy_task)
    assert tool.name == "read_progress"
    
    result = tool()
    assert "Handover check: progress verified." in result

    # 检查在 Toolbox 中注册并获取
    tb = Toolbox(dummy_task, "03-coding")
    tools = tb.get_available_tools()
    tool_names = [t.__name__ for t in tools]
    assert "read_progress" in tool_names
