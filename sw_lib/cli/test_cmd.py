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
from ..core.bootstrap import bootstrap

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


def cmd_test(args):
    """sw test: 黑盒端到端测试 — 模拟 sw monitor 交互"""
    task_name = getattr(args, "name", "") or f"e2e-{int(time.time())}"
    use_mock = not getattr(args, "no_mock", False)

    hdr(f"🧪 HarnessFlow E2E: {task_name}")
    print(f"  Agent: {green('MockAgent') if use_mock else yellow('Real')}")

    _cleanup_repo()

    from ..core.config import _manager
    _manager.reload()
    if use_mock:
        _manager._config.mock_agent.enabled = True
        # Ensure review routes to archive for clean test flow
        _manager._config.mock_agent.review_route = "05-Archive"
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
        from ..core.engine import WorkflowEngine

        st = read_state(task_name)
        current_stage = st.get("stage", "01-brainstorming")
        current_idx = int(st.get("stage_idx", 0))

        callbacks = {
            "add_log": lambda s, m: _record_error(errors, s, m),
            "is_running": lambda: True,
            "on_ask_user": _make_auto_answer(),
        }
        engine = WorkflowEngine(task_name, current_stage, current_idx, "", callbacks)

        # ── 2. sw monitor (主循环) ──
        print(f"  sw monitor...")
        engine.run_stage()

        advance_count = 0
        while advance_count < max_advances and stage_completed < max_stages:
            time.sleep(1)

            status = getattr(engine.agent, 'status', 'idle') if engine.agent else 'idle'

            if status == 'waiting':
                # Agent 在等待用户回答 → auto-answer "A"
                engine.answer("A")
                time.sleep(1)
                continue

            if status == 'idle':
                # Agent 空闲 → 当前阶段完成
                engine.save_stage_output()
                stage_completed += 1
                print(f"    ✓ {STAGE_NAMES[current_idx]} 完成")

                if current_idx >= max_stages - 1:
                    break  # archive complete

                # 检查是否有错误（agent 输出中含 error）
                agent_errors = [e for e in errors if 'error' in e.lower()]
                if agent_errors:
                    print(f"    {red('⚠')} 阶段有 {len(agent_errors)} 个异常记录")

                # /advance
                engine.handle_command("advance")
                advance_count += 1
                time.sleep(1)

                # 更新当前阶段
                st = read_state(task_name)
                current_stage = st.get("stage", current_stage)
                current_idx = int(st.get("stage_idx", current_idx))
                continue

            if status == 'error':
                errors.append(f"[{current_stage}] Agent error")
                engine.shutdown()
                break

        # ── 3. 验证 ──
        print()
        hdr("验证")

        # 检查每个阶段文件是否有产出
        task_dir = TASKS / task_name
        stages_ok = 0
        for s in ["01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"]:
            sf = task_dir / f"{s}.md"
            if sf.exists() and "## 🤖 AI Output" in sf.read_text(encoding="utf-8"):
                stages_ok += 1
                print(f"  {green('✓')} {s}")
            else:
                print(f"  {red('✗')} {s} — 无有效产出")

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
