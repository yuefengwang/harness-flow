"""sw test — 端到端集成测试。

模拟用户在 sw monitor 中的操作流程：
sw init → engine.run_stage() → auto-answer → /advance → ... → archive → verify
"""
import shutil, time, queue as qmod
from pathlib import Path
from typing import List

from ..core.service import _service, TaskError
from ..core.config import TASKS, STAGE_NAMES, ROOT
from ..core.state import read_state, write_state, remove_task_summary
from ..core.utils import green, red, yellow, hdr, die, now

TEST_CONTEXT = (
    "Build a CLI note manager tool in Python. It should: "
    "accept add/list/delete/search commands, store notes in JSON, support tags."
)


def _make_auto_answer():
    """返回 auto_answer 回调：总是选第一个选项。"""
    def auto_answer(questions, res_queue):
        answers = []
        for q in questions:
            opts = q.get("options", [])
            answers.append(opts[0].split(".")[0].strip() if opts and "." in opts[0]
                           else (opts[0][:1] if opts else "A"))
        res_queue.put(answers)
    return auto_answer


def _mock_gate_pass(task_name: str, stage: str):
    """模拟用户勾选 Gate 下的所有复选框，并填写 Route 字段。"""
    path = TASKS / task_name / f"{stage}.md"
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if "[ ]" in content:
            content = content.replace("[ ]", "[x]")
        # 04-review: fill in Route field (mock always routes to 05-Archive)
        if stage == "04-review":
            content = content.replace("- **Route**: `___`", "- **Route**: `05-Archive`", 1)
        path.write_text(content, encoding="utf-8")


def cmd_test(args):
    """sw test: 黑盒端到端测试 — 模拟 sw monitor 交互"""
    from ..core.bootstrap import bootstrap  # 惰性：避免非 test 命令也拉起 langgraph
    task_name = getattr(args, "name", "") or f"e2e-{int(time.time())}"

    _cleanup_repo()

    from ..core.config import _manager
    _manager.reload()

    # 处理 mock/no-mock 参数（优先级: CLI 输入 > config.yaml）
    no_mock_flag = getattr(args, "no_mock", False)
    mock_flag = getattr(args, "mock", False)
    if no_mock_flag:
        use_mock = False
        _manager.config.mock_agent.enabled = False
    elif mock_flag:
        use_mock = True
        _manager.config.mock_agent.enabled = True
        # Ensure review routes to archive for clean test flow
        _manager.config.mock_agent.review_route = "05-Archive"
    else:
        # 未指定，使用 config.yaml 的默认值
        use_mock = _manager.config.mock_agent.enabled
        if use_mock:
            _manager.config.mock_agent.review_route = "05-Archive"

    hdr(f"🧪 HarnessFlow E2E: {task_name}")
    print(f"  Agent: {green('MockAgent') if use_mock else yellow('Real')}")

    bootstrap()

    # ── 1. sw init ──
    print("  sw init...")
    try:
        _service.create_task(task_name, "feature", context=TEST_CONTEXT,
                              agent="", target_dir="repo/test-app")
    except TaskError as e:
        die(f"创建失败: {e}")

    errors: List[str] = []
    stage_completed = 0
    max_stages = 5
    max_advances = max_stages * 3  # allow reroute loops

    try:
        from ..workflow.runtime import WorkflowRuntime
        from ..workflow.base import StageInput
        import threading

        st = read_state(task_name)
        current_stage = st.get("stage", "01-brainstorming")
        current_idx = int(st.get("stage_idx", 0))

        callbacks = {
            "add_log": lambda s, m: _record_error(errors, s, m),
            "is_running": lambda: True,
            "on_ask_user": _make_auto_answer(),
        }

        # ── 2. sw monitor (主循环) ──
        print(f"  sw monitor...")
        
        chain = WorkflowRuntime.get_executor()
        if not chain:
            die("WorkflowRuntime 未初始化")

        # Start the first stage
        stage_input = StageInput(
            task_name=task_name,
            stage=current_stage,
            stage_idx=current_idx,
            metadata={"callbacks": callbacks}
        )
        threading.Thread(target=chain.invoke, args=(stage_input,), daemon=True).start()

        advance_count = 0
        while advance_count < max_advances and stage_completed < max_stages:
            time.sleep(1)

            # Get status from active agent in the chain
            agent = chain.active_stage.active_agent if chain.active_stage else None
            status = getattr(agent, 'status', 'idle') if agent else 'idle'
            print(f"  [TRACE] loop: status={status!r}, stage_completed={stage_completed}, agent_active={chain.active_stage is not None and agent is not None}, active_stage_set={chain.active_stage is not None}")

            if status == 'waiting':
                # Agent 在等待用户回答 → auto-answer
                _service.add_answer(task_name, "A")
                time.sleep(1)
                continue

            if status == 'idle':
                # Check if current stage is done
                st = read_state(task_name)
                current_stage = st.get("stage")
                current_idx = int(st.get("stage_idx", 0))
                stage_status = st.get("stage_status")

                if current_idx >= max_stages - 1 and stage_status == "Finished":
                    stage_completed = max_stages
                    print(f"    ✓ {STAGE_NAMES[current_idx]} 完成")
                    break  # archive complete

                # 检查是否有错误
                agent_errors = [e for e in errors if 'error' in e.lower()]
                if agent_errors:
                    print(f"    {red('⚠')} 阶段有 {len(agent_errors)} 个异常记录")

                print(f"    ✓ {STAGE_NAMES[current_idx]} 完成，准备推进...")
                
                # Signal multi-turn agent to finalize (Direction A)
                if chain.active_stage and chain.active_stage.active_agent:
                    chain.active_stage._stage_done.set()
                    chain.active_stage._agent_finalized.wait(timeout=15)
                    chain.active_stage._invoke_done.wait(timeout=30)
                
                # Mock checkboxes for the gate
                _mock_gate_pass(task_name, current_stage)
                
                try:
                    _service.advance_stage(task_name)
                except Exception as e:
                    print(f"    {red('✗')} 推进失败: {e}")
                    break

                advance_count += 1
                stage_completed += 1
                
                # Restart chain for the next stage
                st = read_state(task_name)
                next_stage = st.get("stage")
                next_idx = int(st.get("stage_idx"))
                
                print(f"    推进到: {STAGE_NAMES[next_idx]}")
                
                stage_input = StageInput(
                    task_name=task_name,
                    stage=next_stage,
                    stage_idx=next_idx,
                    metadata={"callbacks": callbacks}
                )
                threading.Thread(target=chain.invoke, args=(stage_input,), daemon=True).start()
                time.sleep(2)
                continue

            if status == 'error':
                errors.append(f"[{current_stage}] Agent error")
                break

        # ── 3. 验证 ──
        print()
        hdr("验证")

        # 检查每个阶段文件是否有产出
        task_dir = TASKS / task_name
        stages_ok = 0
        for s in ["01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"]:
            sf = task_dir / f"{s}.md"
            content = sf.read_text(encoding="utf-8", errors="replace") if sf.exists() else ""
            has_output = "## 🤖 AI Output" in content
            if has_output:
                stages_ok += 1
                print(f"  {green('✓')} {s}")
            else:
                issue = "文件不存在" if not sf.exists() else "无 AI Output 标记"
                print(f"  {red('✗')} {s} — {issue} (文件大小={sf.stat().st_size if sf.exists() else 0}B)")

        # 检查代码文件
        py_files = _find_code_files(task_name)
        code_ok = bool(py_files)
        if code_ok:
            print(f"  {green('✓')} 代码: {len(py_files)} 个文件")
        elif use_mock:
            print(f"  {yellow('ℹ')} MockAgent 不生成代码文件")
        else:
            print(f"  {red('✗')} 未生成代码文件")

        # 错误日志
        if errors:
            print()
            print(f"  {yellow('⚠')} 运行日志中的异常 ({len(errors)} 条):")
            for e in errors[:10]:
                print(f"    - {e[:150]}")

        # 汇总
        print()
        hdr("结果")
        passed = stages_ok + (1 if code_ok or use_mock else 0)
        failed = (max_stages - stages_ok) + (0 if code_ok or use_mock else 1)
        print(f"  通过: {green(str(passed))} | 失败: {red(str(failed))}")
        if stages_ok < max_stages:
            die(f"❌ 仅完成 {stages_ok}/{max_stages} 阶段")

    finally:
        engine.shutdown() if 'engine' in dir() else None
        _manager.reload()
        _cleanup_repo()
        td = TASKS / task_name
        if td.exists():
            shutil.rmtree(td, ignore_errors=True)
        remove_task_summary(task_name)
        print(f"  {green('✓')} 清理完成")


def _record_error(errors: List[str], source: str, msg: str):
    """记录运行日志中的异常行。"""
    if source in ("error",):
        errors.append(f"[{source}] {msg}")


def _cleanup_repo():
    repo_dir = ROOT / "repo" / "test-app"
    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)


def _find_code_files(task_name: str) -> List[Path]:
    st = read_state(task_name)
    target_dir = st.get("target_dir", "repo/test-app")
    repo_path = Path(target_dir)
    if not repo_path.is_absolute():
        repo_path = ROOT / target_dir
    if not repo_path.exists():
        return []
    return list(repo_path.rglob("*.py"))
