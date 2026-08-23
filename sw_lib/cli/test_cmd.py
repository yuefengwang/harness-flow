"""sw test — 端到端集成测试。

模拟用户在 sw monitor 中的操作流程：
sw init → engine.run_stage() → auto-answer → /advance → ... → archive → verify
"""
import shutil, time
from pathlib import Path
from typing import List

from ..core.service import _service, TaskError
from ..core.config import TASKS, STAGES, STAGE_NAMES, ROOT
from ..core.state import read_state, remove_task_summary
from ..core.utils import green, red, yellow, hdr, die

TEST_CONTEXT = (
    "Build a CLI note manager tool in Python. It should: "
    "accept add/list/delete/search commands, store notes in JSON, support tags."
)

# 补拍板记录的上限。选项组是有限的，循环不该无界 —— 若真到了上限说明
# 校验里有 mock 补不掉的阻塞项，交给后面的推进去报错。
_MAX_MOCK_DECISIONS = 20


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
    """模拟用户签署本阶段门禁、拍板选项组、并填写 Route。

    只写 `.state`：门禁、Route、拍板记录的唯一真源在那里，Markdown 由
    `render_gate_section` 单向渲染。改 Markdown 的复选框对推进校验毫无作用。
    """
    from ..workflow import stage_state as ss
    from ..workflow.utils import check_stage_compliance

    ss.sign_gate(task_name, stage, by="mock")
    if stage == "04-review":
        ss.write_route(task_name, "05-Archive", by="mock")

    # 选项组按「未拍板组数 - 已记录决策数」判定，缺几组补几组。这里不去数
    # Markdown，而是直接问真实校验还剩什么 —— 复用生产判定，免得 mock 自带
    # 一份会各自漂移的计数逻辑。
    stage_idx = STAGES.index(stage) if stage in STAGES else 0
    for n in range(_MAX_MOCK_DECISIONS):
        _, todo = check_stage_compliance(task_name, stage, stage_idx)
        if not any("选项组" in t for t in todo):
            break
        ss.record_decision(task_name, stage, f"mock-choice-{n + 1}", "A", by="mock")

    ss.render_gate_section(task_name, stage)


def cmd_test(args):
    """sw test: 黑盒端到端测试 — 模拟 sw monitor 交互"""
    from ..core.bootstrap import bootstrap  # 惰性：避免非 test 命令也拉起 langgraph
    task_name = getattr(args, "name", "") or f"e2e-{int(time.time())}"

    _cleanup_repo()

    from ..core.config import _manager
    _manager.reload()

    # 一律用 MockAgent，且就地覆盖 config.yaml 的相关项。真实 agent 的输出
    # 不可复现，拿它当测试无法区分「代码回归」和「模型这次答得不一样」；
    # review_route 也必须写死 —— 从 config.yaml 读会让 e2e 的结论跟着谁改过
    # 配置文件而变。测试的输入必须由测试自己决定。
    _manager.config.mock_agent.enabled = True
    _manager.config.mock_agent.review_route = "05-Archive"

    hdr(f"🧪 HarnessFlow E2E: {task_name}")
    print(f"  Agent: {green('MockAgent')} (scripted)")

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

        # 检查代码文件。MockAgent 的 03-coding 场景会真的写出 mocknote.py /
        # test_mocknote.py / README.md（03 的硬校验拒绝空产出），所以这里是
        # 硬要求 —— 以前给 mock 开的豁免只会掩盖「产出没落盘」这类真问题。
        py_files = _find_code_files(task_name)
        code_ok = bool(py_files)
        if code_ok:
            print(f"  {green('✓')} 代码: {len(py_files)} 个文件")
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
        passed = stages_ok + (1 if code_ok else 0)
        failed = (max_stages - stages_ok) + (0 if code_ok else 1)
        print(f"  通过: {green(str(passed))} | 失败: {red(str(failed))}")
        if stages_ok < max_stages:
            die(f"❌ 仅完成 {stages_ok}/{max_stages} 阶段")

    finally:
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
