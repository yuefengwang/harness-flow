import pytest
from sw_lib.core.engine import WorkflowEngine

def test_build_context_with_template(dummy_task, agent_callbacks):
    engine = WorkflowEngine(dummy_task, "01-brainstorming", 0, "", agent_callbacks)
    ctx = engine._build_context()
    if ctx:
        assert any(keyword in ctx for keyword in ["头脑风暴", "Brainstorming", "hook", "强制规则"])

def test_command_dispatch(dummy_task, agent_callbacks):
    logs = []
    callbacks = dict(agent_callbacks)
    callbacks["add_log"] = lambda s, m: logs.append((s, m))
    engine = WorkflowEngine(dummy_task, "01-brainstorming", 0, "", callbacks)
    engine.handle_command("status")
    sw_logs = [msg for src, msg in logs if src == "sw"]
    assert any("stage=" in m for m in sw_logs)

def test_build_context_loads_progress_txt(dummy_task, agent_callbacks):
    from sw_lib.core.config import TASKS
    task_dir = TASKS / dummy_task
    progress_file = task_dir / "progress.txt"
    progress_file.write_text("This is an evolution checkpoint. Implement SDD first.", encoding="utf-8")

    engine = WorkflowEngine(dummy_task, "01-brainstorming", 0, "", agent_callbacks)
    ctx = engine._build_context()
    assert ctx is not None
    assert "This is an evolution checkpoint." in ctx
    assert "progress.txt" in ctx
    assert "Bootstrap Command" in ctx
    assert "当前的首要任务" in ctx

def test_extract_progress_section_and_save(dummy_task, agent_callbacks):
    from sw_lib.core.config import TASKS
    from sw_lib.core.engine import OutputExtractor
    task_dir = TASKS / dummy_task
    progress_file = task_dir / "progress.txt"
    if progress_file.exists():
        progress_file.unlink()

    output_lines = [
        ("system", "=== Start ==="),
        ("agent", "I have implemented the core engine logic.\n\n## Progress\n- Worked on progress handover mechanisms.\n- Everything passes green.\n\n## Other Title\n- Unrelated stuff."),
        ("system", "=== End ===")
    ]

    logs = []
    add_log = lambda s, m: logs.append(m)
    saved = OutputExtractor.extract_and_save(dummy_task, "01-brainstorming", output_lines, add_log)
    assert saved
    assert progress_file.exists()
    
    content = progress_file.read_text(encoding="utf-8")
    assert "Worked on progress handover" in content
    assert "Progress" in content
    assert "Unrelated stuff" not in content
    assert any("自动更新到 progress.txt" in m for m in logs)
