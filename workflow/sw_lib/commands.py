"""sw — Simple Workflow CLI (统一入口)

用法: ./sw <command> [options]
  ./sw init    --type=feature --name=<id> [--context=<text>] [--agent=<agent>]
  ./sw init                              # 交互模式
  ./sw monitor --name=<id>               # Rich TUI 监控面板 + Agent 对话
  ./sw status  [--name=<id>]
  ./sw next    [--name=<id>] [--agent=<agent>]   # 注入上下文 (monitor 替代交互)
  ./sw advance [--name=<id>] [--force]           # 校验+推进阶段
  ./sw resume  --name=<id>
  ./sw list    [--trash]
  ./sw remove  --name=<id>
  ./sw restore --name=<id>
  ./sw answer  --name=<id> --text=<reply>   # 回复 Agent 提问
"""

import sys
import shutil
import subprocess

from .config import ROOT, WORKFLOW, TASKS, TPLS, STAGES, STAGE_NAMES, HAS_RICH, TRASH, resolve_agent_type
from .state import (
    state_path, read_state, write_state,
    update_status_md_active, clear_status_md_active,
    update_status_md_stage, get_active_from_status,
    StageValidator,
)
from .utils import (
    green, red, yellow, blue,
    ok, err, warn, hdr, die,
    prompt, prompt_yn,
    now, sanitize_name, sw_log,
)
from .tui import MonitorTUI


# ── commands ──

def cmd_init(args):
    """创建新任务"""
    task_type = args.type or "feature"
    name = args.name or ""
    session = args.session or ""
    agent = getattr(args, "agent", "") or ""
    context = getattr(args, "context", "") or ""
    interactive = getattr(args, "interactive", False)

    # 交互模式：无参数或 --interactive
    if interactive or not any([args.name]):
        interactive = True
        hdr("创建新任务 (交互模式)")
        while not name:
            name = prompt("任务名称 (必填)")
        task_type = prompt("任务类型 (feature/bugfix/refactor/chore)", task_type)
        session = prompt("Session ID", "N/A")

        from .config import load_harness_config, resolve_agent_type, resolve_agent_model
        cfg = load_harness_config()
        roles = cfg.get("harness", {}).get("roles", {})
        stage_roles = cfg.get("harness", {}).get("stage_roles", {})

        if roles:
            default_role = stage_roles.get("01-brainstorming", "")
            if default_role and default_role in roles:
                default_info = roles[default_role]
                default_agent = default_info.get("agent", "")
                default_model = default_info.get("model", "")
                default_desc = default_info.get("description", "")
            else:
                first_role = next(iter(roles))
                default_agent = roles[first_role].get("agent", "")
                default_model = roles[first_role].get("model", "")
                default_desc = roles[first_role].get("description", "")
                default_role = first_role

            print(f"  {blue('→')} 默认角色: {default_role} ({default_desc})")
            print(f"  {blue('→')} Agent 类型: {default_agent}, 模型: {default_model}")
            role_descriptions = " / ".join(f"{k}({v.get('description', '')})" for k, v in roles.items())
            print(f"  {blue('→')} 可选角色: {role_descriptions}")
            agent = prompt(f"指定角色 (回车使用默认 {default_role})", default_role)
            if not agent:
                agent = default_role
        else:
            agent = prompt("AI 角色 (gemini/opencode/claudecode)", "gemini")

        if not context:
            print(f"  {blue('→')} 需求描述 (回车结束，空行直接跳过):")
            context = input(f"  {blue('→')} ").strip()
            context = context.encode("utf-8", "surrogateescape").decode("utf-8", "replace")

    if not name:
        die("缺少 --name")

    # 清理任务名（移除非法 Unicode 字符，macOS Python 3.9 兼容）
    raw_name = name
    name = sanitize_name(name)
    if name != raw_name:
        warn(f"任务名已清理: '{raw_name}' → '{name}'")
    if not name.strip("-_. ") or name == "task":
        die("任务名无效，请使用英文字母、数字、短横或下划线")

    task_dir = TASKS / name

    # 任务已存在 → 交互模式提供 resume
    if task_dir.exists():
        st = read_state(name)
        stage = st.get("stage", "unknown")
        if interactive:
            warn(f"任务已存在: {name} ({stage})")
            if prompt_yn("是否恢复 (resume)?", "y"):
                sys.exit(cmd_resume_impl(name))
        die(f"任务已存在: {name}，使用 ./sw resume --name={name} 恢复")

    # 创建任务目录
    task_dir.mkdir(parents=True, exist_ok=True)

    # 复制模板
    for tpl in TPLS.glob("*.md"):
        shutil.copy2(tpl, task_dir / tpl.name)

    # 创建 .input 文件（Agent 交互管道）
    (task_dir / ".input").touch(exist_ok=True)

    # 保存需求上下文（清理 surrogate 字符）
    if context:
        context = context.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
        (task_dir / ".context").write_text(context)

    # 写入 .state
    write_state(name, {
        "id": name,
        "type": task_type,
        "session": session or "N/A",
        "agent": agent or "N/A",
        "stage": '"01-brainstorming"',
        "stage_idx": "0",
        "stage_status": "pending",
        "created_at": now(),
        "updated_at": now(),
    })

    # 更新 STATUS.md
    try:
        update_status_md_active(name)
    except Exception:
        pass

    sw_log(name, f"task created (type={task_type}, agent={agent or 'none'})")
    if context:
        sw_log(name, f"context saved ({len(context)} chars)")
    sw_log(name, "stage: 01-brainstorming → pending")

    hdr(f"任务已创建: {name}")
    ok("Stage: 01-头脑风暴")
    if agent and agent not in ("N/A", ""):
        print(f"  Agent: {agent}")
    print(f"  进入监控: ./sw monitor --name={name}")

    # 交互终端 → 自动进入 TUI
    if sys.stdin.isatty() and sys.stdout.isatty():
        resolved_type = resolve_agent_type("01-brainstorming", agent)
        if prompt_yn("是否现在进入监控面板?", "y"):
            MonitorTUI(name, "01-brainstorming", 0, agent).run()


def cmd_resume_impl(name: str) -> int:
    """resume 逻辑（供 init 交互模式复用）"""
    st = read_state(name)
    if not st:
        return 1
    stage = st.get("stage", "unknown")
    hdr(f"恢复任务: {name}")
    print(f"  Stage: {stage}")
    print(f"  状态文件: {state_path(name)}")
    print(f"  继续: hooks/{stage}.md → templates/{stage}.md")
    print(f"  完成后: ./sw advance --name={name}")
    return 0


def cmd_resume(args):
    """恢复已有任务"""
    name = args.name or ""
    if not name:
        die("请指定任务名: ./sw resume --name=<id>")
    if not state_path(name).exists():
        die(f"任务不存在: {name}")
    return cmd_resume_impl(name)


def cmd_status(args):
    """显示任务状态"""
    name = args.name or ""
    if not name:
        name = get_active_from_status()
    if not name or name == "无":
        die("无活跃任务")
    st = read_state(name)
    stage = st.get("stage", "unknown")
    status = st.get("stage_status", "N/A")
    updated = st.get("updated_at", "N/A")
    print(f"任务: {name} | Stage: {stage} | 状态: {status} | {updated}")


def cmd_advance(args):
    """校验当前阶段 → 用户确认 → 推进到下一阶段"""
    name = getattr(args, "name", "") or ""
    force = getattr(args, "force", False)

    # 解析任务名
    if name and (TASKS / name / ".state").exists():
        pass
    elif not name:
        name = get_active_from_status()
        if not name:
            die("无活跃任务，请指定: ./sw advance --name=<task>")

    st = read_state(name)
    if not st:
        die(f"任务不存在: {name}")

    idx = int(st.get("stage_idx", 0))
    cur_stage = STAGES[idx]
    cur_name = STAGE_NAMES[idx]
    cur_status = st.get("stage_status", "pending")

    # 已完成所有阶段
    if idx >= len(STAGES) - 1:
        ok("任务已完成 (05-归档)")
        return

    # 如果当前阶段是 pending，提示先 sw next
    if cur_status == "pending" and not force:
        die(f"当前阶段 '{cur_name}' 尚未开始\n  请先: ./sw next --name={name}")

    # 阶段校验
    task_dir = TASKS / name
    hdr(f"阶段校验: {cur_stage} ({cur_name})")

    done_items, todo_items = StageValidator.check(task_dir, cur_stage, idx)

    if done_items:
        for item in done_items:
            print(f"  {green('[✓]')} {item}")
    if todo_items:
        for item in todo_items:
            print(f"  {yellow('[!]')} {item}")

    # CLI 强阻断校验 (Phase 3: CLI Strong Block Validation)
    hook_script = ROOT / "hooks" / f"check_{cur_stage}.sh"
    if hook_script.exists():
        if force:
            warn(f"系统硬校验已跳过 (--force): {hook_script.name}")
        else:
            hdr(f"系统硬校验: {hook_script.name}")
            try:
                res = subprocess.run([str(hook_script), task_dir.name], cwd=str(ROOT), check=False)
                if res.returncode != 0:
                    die(f"校验失败 (exit {res.returncode}): 必须满足 {hook_script.name} 的所有条件才能推进。")
                else:
                    ok("硬校验通过")
            except Exception as e:
                die(f"执行校验脚本异常: {e}")

    # 用户确认
    passed = len(todo_items) == 0
    if not passed and not force:
        warn("存在未完成项，仍要推进吗？")
        if not prompt_yn("确认推进到下一阶段?", "n"):
            print("  已取消")
            return

    # 推进
    ni = idx + 1
    ns = STAGES[ni]
    nl = STAGE_NAMES[ni]

    st["stage"] = f'"{ns}"'
    st["stage_idx"] = str(ni)
    st["stage_status"] = "pending"
    st["updated_at"] = now()
    write_state(name, st)

    try:
        update_status_md_stage(ni)
    except Exception:
        pass

    sw_log(name, f"stage: {cur_stage} → completed (check: {len(done_items)} done, {len(todo_items)} todo)")
    sw_log(name, f"advance → {ns} ({nl})")

    hdr(f"阶段推进 → {nl}")
    print(f"  下一步: ./sw next --name={name}")
    print(f"  手动启动: ./sw advance --name={name} --force  (跳过校验)")


def cmd_next(args):
    """为当前 stage 注入上下文并标记 in_progress"""
    name = getattr(args, "name", "") or ""
    agent_override = getattr(args, "agent", "") or ""

    # 解析任务名
    if name and (TASKS / name / ".state").exists():
        pass
    elif not name:
        name = get_active_from_status()
        if not name:
            die("无活跃任务，请指定: ./sw next --name=<task>")

    st = read_state(name)
    if not st:
        die(f"任务不存在: {name}")

    idx = int(st.get("stage_idx", 0))
    cur_stage = STAGES[idx]
    cur_name = STAGE_NAMES[idx]
    cur_status = st.get("stage_status", "pending")
    agent = agent_override or st.get("agent", "").strip('"') or ""

    # 检查阶段状态
    if cur_status == "completed":
        die(f"当前阶段 '{cur_name}' 已完成\n  请先: ./sw advance --name={name}")

    # 如果已经是 in_progress，警告可能重复启动
    if cur_status == "in_progress":
        warn(f"阶段 '{cur_name}' 已在运行中")
        if not prompt_yn("是否重新启动 Agent?", "n"):
            print("  已取消")
            return

    # 显示启动信息
    hdr(f"启动阶段: {cur_stage} ({cur_name})")
    print(f"  任务: {name}")
    prev = STAGES[idx - 1] if idx > 0 else None
    if prev:
        print(f"  前序产出: workflow/tasks/{name}/{prev}.md")
    print(f"  当前模板: workflow/tasks/{name}/{cur_stage}.md")
    print(f"  强制规则: workflow/hooks/{cur_stage}.md")
    if agent:
        print(f"  Agent: {agent}")

    # 确认
    if sys.stdin.isatty() and sys.stdout.isatty():
        if not prompt_yn("确认启动?", "y"):
            print("  已取消")
            return

    # 更新状态为 in_progress
    st["stage_status"] = "in_progress"
    st["updated_at"] = now()
    write_state(name, st)

    task_dir = TASKS / name

    # Phase 2: 动态技能注入 (Dynamic Skill Injection)
    role_prompts = {
        "01-brainstorming": "你是需求分析师 (Analyst)。尽量只使用查询工具 (`read_file`, `grep_search`, `ask_user`)。不要使用写文件或 Shell 脚本修改代码，除非得到用户显式允许。你的唯一产出是与用户确认的需求文档。",
        "02-planning": "你是架构师 (Architect)。尽量只使用查询工具。不要修改源码文件。负责架构设计和任务拆解，产出设计文档。",
        "03-coding": "你是纯粹执行者 (Developer)。全面开放代码读写和终端执行权限。严格按照 02 阶段的设计进行编码，并优先遵循 TDD（测试驱动开发）。不要偏离设计，遇到重大问题请询问。",
        "04-review": "你是严苛审查员 (Reviewer)。尽量只读不写，使用测试命令验证结果，并产出 Review 意见。",
        "05-archive": "你是文档专员 (Archivist)。仅限修改工作流状态文档和 README，不要修改业务源码。"
    }
    
    task_gemini_md = task_dir / "GEMINI.md"
    skill_text = role_prompts.get(cur_stage, "你是一个 AI 助手，请遵循模板执行工作。")
    task_gemini_md.write_text(f"# 当前工作流阶段: {cur_stage} ({cur_name})\n\n<activated_skill name=\"{cur_name}_role\">\n{skill_text}\n</activated_skill>\n\n**重要纪律：** 严格遵循上述角色设定，不要跳出当前阶段的职责范围。")

    # ── 注入上下文到 .input ──
    input_file = task_dir / ".input"
    context_file = task_dir / ".context"
    prev_stage = STAGES[idx - 1] if idx > 0 else None

    with open(input_file, "a") as f:
        f.write(f"[{now()}] system | === Stage: {cur_stage} ({cur_name}) ===\n")

        # 注入原始需求上下文
        if context_file.exists():
            ctx_text = context_file.read_text().strip()
            if ctx_text:
                f.write(f"[{now()}] system | 任务需求上下文:\n")
                for line in ctx_text.splitlines():
                    f.write(f"[{now()}] system |   {line}\n")

        # 注入前一阶段产出
        if prev_stage:
            prev_tpl = TASKS / name / f"{prev_stage}.md"
            if prev_tpl.exists():
                prev_content = prev_tpl.read_text()[:3000]
                f.write(f"[{now()}] system | --- 前一阶段产出 ({prev_stage}) ---\n")
                for line in prev_content.splitlines()[:80]:
                    f.write(f"[{now()}] system |   {line}\n")
                f.write(f"[{now()}] system | --- END ---\n")

        f.write(f"[{now()}] system | 当前模板: {cur_stage}.md\n")
        f.write(f"[{now()}] system | 强制规则: hooks/{cur_stage}.md\n")
        f.write(f"[{now()}] system | 请开始 {cur_name} 阶段的工作。如有问题请提出选项供用户选择。\n")

    sw_log(name, f"context injected to .input ({cur_stage})")
    sw_log(name, f"stage: {cur_stage} → in_progress", "sw")
    if agent:
        sw_log(name, f"agent launching: {agent}", "sw")

    # 提示用户启动 monitor（替代旧 tmux 方案）
    print(f"\n  {green('▸')} 运行监控面板: ./sw monitor --name={name}")
    print(f"  {green('▸')} 完成后执行: ./sw advance --name={name}")


def cmd_list(args):
    """列出任务"""
    show_trash = getattr(args, "trash", False)

    if show_trash:
        print("已移除任务 (.trash):")
        found = False
        if TRASH.is_dir():
            for d in sorted(TRASH.iterdir()):
                if not d.is_dir():
                    continue
                sf = d / ".state"
                data = {}
                if sf.exists():
                    for line in sf.read_text().splitlines():
                        if ":" in line:
                            k, _, v = line.partition(":")
                            data[k.strip()] = v.strip().strip('"')
                stage = data.get("stage", "N/A")
                removed = data.get("removed_at", "N/A")
                print(f"  {d.name:<30}  {stage:<20}  移除于: {removed}")
                found = True
        if not found:
            print("  (回收站为空)")
    else:
        print("活跃任务:")
        found = False
        if TASKS.is_dir():
            for d in sorted(TASKS.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                st = read_state(d.name)
                stage = st.get("stage", "N/A")
                print(f"  {d.name:<30}  {stage}")
                found = True
        if not found:
            print("  (无活跃任务)")


def cmd_remove(args):
    """移任务到回收站"""
    name = getattr(args, "name", "") or ""
    if not name:
        die("缺少 --name")

    task_dir = TASKS / name
    if not task_dir.is_dir():
        if (TRASH / name).is_dir():
            die(f"任务已在回收站: {name}")
        die(f"任务不存在: {name}")

    TRASH.mkdir(parents=True, exist_ok=True)

    # 追加 removed_at 到 .state
    sf = task_dir / ".state"
    if sf.exists():
        with open(sf, "a") as f:
            f.write(f"removed_at: {now()}\n")

    shutil.move(str(task_dir), str(TRASH / name))

    # 清除 STATUS.md
    try:
        clear_status_md_active(name)
    except Exception:
        pass

    ok(f"任务已移入回收站: {name}")
    warn(f"恢复: ./sw restore --name={name}")
    warn(f"恢复: ./sw restore --name={name}")


def cmd_restore(args):
    """从回收站恢复任务"""
    name = getattr(args, "name", "") or ""
    if not name:
        die("缺少 --name")

    src = TRASH / name
    if not src.is_dir():
        die(f"回收站中无此任务: {name}")
        print("  查看已移除: ./sw list --trash")
        return

    dst = TASKS / name
    if dst.exists():
        die(f"同名任务已存在: {name}，请先移除现有任务")

    shutil.move(str(src), str(dst))

    # 清除 removed_at 标记
    sf = dst / ".state"
    if sf.exists():
        lines = sf.read_text().splitlines()
        lines = [l for l in lines if not l.startswith("removed_at:")]
        sf.write_text("\n".join(lines) + "\n")

    ok(f"任务已恢复: {name}")
    print(f"  继续: ./sw resume --name={name}")

    sw_log(name, f"restored from .trash/ (stage: {read_state(name).get('stage', '?')})")

    # 清理空 .trash
    try:
        if TRASH.is_dir() and not any(TRASH.iterdir()):
            TRASH.rmdir()
    except OSError:
        pass


def cmd_answer(args):
    """用户回复 Agent 的交互问题，写入 .input 并记录日志"""
    name = getattr(args, "name", "") or ""
    text = getattr(args, "text", "") or ""

    if not name:
        name = get_active_from_status()
    if not name:
        die("缺少 --name")

    if not text:
        die("缺少 --text (回复内容)")

    task_dir = TASKS / name
    if not task_dir.is_dir():
        die(f"任务不存在: {name}")

    input_file = task_dir / ".input"
    ts = now()
    line = f"[{ts}] user | {text}\n"
    with open(input_file, "a") as f:
        f.write(line)

    sw_log(name, f"user answer: {text}", "user")
    ok(f"已回复: {text[:60]}{'...' if len(text) > 60 else ''}")


def cmd_monitor(args):
    """启动流式终端监控面板，直连 Agent 进程"""
    name = getattr(args, "name", "") or ""
    if not name:
        name = get_active_from_status()
    if not name:
        die("缺少 --name")

    st = read_state(name)
    if not st:
        die(f"任务不存在: {name}")

    stage = st.get("stage", "").strip('"')
    idx = int(st.get("stage_idx", 0))
    agent = st.get("agent", "").strip('"')
    if agent in ("N/A", ""):
        agent = ""

    MonitorTUI(name, stage, idx, agent).run()


def cmd_usage():
    """打印帮助信息"""
    print(__doc__)
