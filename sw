#!/usr/bin/env python3
"""sw — Simple Workflow CLI (统一入口)

用法: ./sw <command> [options]
  ./sw init    --type=feature --name=<id> [--context=<text>] [--agent=<agent>] [--no-tmux]
  ./sw init                              # 交互模式
  ./sw status  [--name=<id>]
  ./sw next    [--name=<id>] [--agent=<agent>]   # 启动当前阶段 Agent
  ./sw advance [--name=<id>] [--force]           # 校验+推进阶段
  ./sw resume  --name=<id>
  ./sw list    [--trash]
  ./sw remove  --name=<id>
  ./sw restore --name=<id>
  ./sw answer  --name=<id> --text=<reply>   # 回复 Agent 提问
"""

import sys
import os
import re
import shutil
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "workflow" / "tasks"
TPLS = ROOT / "workflow" / "templates"
STATUS = ROOT / "workflow" / "STATUS.md"
TRASH = TASKS / ".trash"

STAGES = ["01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"]
STAGE_NAMES = ["头脑风暴", "规划", "编码", "评审", "归档"]


# ── helpers ──

def state_path(name: str) -> Path:
    return TASKS / name / ".state"

def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def green(s): return f"\033[0;32m{s}\033[0m"
def red(s):   return f"\033[0;31m{s}\033[0m"
def yellow(s): return f"\033[1;33m{s}\033[0m"
def blue(s):  return f"\033[0;34m{s}\033[0m"

def ok(msg):    print(f"{green('[✓]')} {msg}")
def err(msg):   print(f"{red('[✗]')} {msg}", file=sys.stderr)
def warn(msg):  print(f"{yellow('[!]')} {msg}")
def hdr(msg):   print(f"\n{blue('━━━ ' + msg + ' ━━━')}")

def die(msg):
    err(msg)
    sys.exit(1)

def prompt(prompt_text: str, default: str = "") -> str:
    """交互式输入，带默认值提示"""
    if default:
        sys.stdout.write(f"  {blue('→')} {prompt_text} [{default}]: ")
    else:
        sys.stdout.write(f"  {blue('→')} {prompt_text}: ")
    sys.stdout.flush()
    try:
        response = input().strip()
    except (EOFError, KeyboardInterrupt):
        print()
        response = ""
    return response if response else default

def prompt_yn(prompt_text: str, default: str = "y") -> bool:
    """交互式 Y/n 确认"""
    hint = "Y/n" if default == "y" else "y/N"
    sys.stdout.write(f"  {blue('→')} {prompt_text} [{hint}]: ")
    sys.stdout.flush()
    try:
        resp = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        resp = ""
    resp = resp if resp else default
    return resp in ("y", "yes")

def sw_log(name: str, message: str, source: str = "sw"):
    """向任务日志文件追加一行事件（仅在目录已存在时写入）"""
    log_dir = TASKS / name
    if not log_dir.is_dir():
        return  # 目录不存在（如已移除），跳过
    log_file = log_dir / ".log"
    ts = now()
    line = f"[{ts}] {source:<6}| {message}\n"
    with open(log_file, "a") as f:
        f.write(line)

def read_state(name: str) -> dict:
    """读取 .state 文件为字典"""
    sf = state_path(name)
    if not sf.exists():
        return {}
    state = {}
    for line in sf.read_text().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            state[key.strip()] = val.strip().strip('"')
    return state

def write_state(name: str, data: dict):
    """写入 .state 文件"""
    sf = state_path(name)
    sf.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}: {v}" for k, v in data.items()]
    sf.write_text("\n".join(lines) + "\n")

def update_status_md_active(name: str):
    """更新 STATUS.md 中的活动任务和当前阶段"""
    content = STATUS.read_text()
    content = re.sub(r'^(- \*\*活动任务:\*\*)\s*.*', rf'\1 {name}', content, flags=re.MULTILINE)
    content = re.sub(r'^(- \*\*当前阶段:\*\*)\s*.*', r'\1 01-头脑风暴', content, flags=re.MULTILINE)
    STATUS.write_text(content)

def clear_status_md_active(name: str):
    """清除 STATUS.md 中指定任务的活动引用"""
    content = STATUS.read_text()
    content = re.sub(r'^(- \*\*活动任务:\*\*)\s*.*', r'\1 无', content, flags=re.MULTILINE)
    content = re.sub(r'^(- \*\*当前阶段:\*\*)\s*.*', r'\1 N/A', content, flags=re.MULTILINE)
    STATUS.write_text(content)

def update_status_md_stage(idx: int):
    """推进 STATUS.md 中的当前阶段"""
    content = STATUS.read_text()
    new_stage = f"0{idx+1}-{STAGE_NAMES[idx]}"
    content = re.sub(r'^(- \*\*当前阶段:\*\*)\s*.*', rf'\1 {new_stage}', content, flags=re.MULTILINE)
    STATUS.write_text(content)

def get_active_from_status() -> Optional[str]:
    """从 STATUS.md 读取当前活动任务名"""
    content = STATUS.read_text()
    m = re.search(r'\*\*活动任务:\*\*\s*(.+?)(?:\*\*)?$', content, re.MULTILINE)
    if m:
        name = m.group(1).strip().rstrip("*")
        if name and name != "无":
            return name
    return None

def launch_tmux(name: str, agent: str = "") -> bool:
    """创建 tmux 会话：左侧 bash，右侧 AI Agent"""
    if not shutil.which("tmux"):
        warn("tmux 未安装，跳过会话创建 (brew install tmux)")
        return False

    session = f"sw-{name}"
    try:
        # 清理同名旧会话
        subprocess.run(["tmux", "kill-session", "-t", session],
                       capture_output=True)

        # 创建 detached session
        subprocess.run(["tmux", "new-session", "-d", "-s", session,
                        "-c", str(ROOT)], check=True)
        subprocess.run(["tmux", "rename-window", "-t", session, "sw"], check=True)

        # 获取窗口和 pane 索引
        win = subprocess.check_output(
            ["tmux", "display", "-t", session, "-p", "#{window_index}"],
            text=True).strip()
        left = subprocess.check_output(
            ["tmux", "display", "-t", session, "-p", "#{pane_index}"],
            text=True).strip()
        right = subprocess.check_output(
            ["tmux", "split-window", "-h", "-P", "-F", "#{pane_index}",
             "-t", session], text=True).strip()

        # 右侧 pane: AI Agent 或提示
        if agent:
            subprocess.run(["tmux", "send-keys", "-t", f"{session}:{win}.{right}",
                           agent, "Enter"])
        else:
            subprocess.run(["tmux", "send-keys", "-t", f"{session}:{win}.{right}",
                           "echo '🤖 在此启动 AI Agent: opencode / claude / gemini'", "Enter"])

        # 选中左侧 pane，发送任务提示
        subprocess.run(["tmux", "select-pane", "-t", f"{session}:{win}.{left}"])
        subprocess.run(["tmux", "send-keys", "-t", f"{session}:{win}.{left}",
                       "C-l"])
        subprocess.run(["tmux", "send-keys", "-t", f"{session}:{win}.{left}",
                       f"echo '📋 任务: {name} | Stage: 01-头脑风暴'", "Enter"])

        # 附加到会话
        if not os.environ.get("TMUX"):
            ok(f"tmux 会话: {session}")
            os.execlp("tmux", "tmux", "attach", "-t", session)
        else:
            warn(f"已在 tmux 中 → 切换到 {session}")
            subprocess.run(["tmux", "switch-client", "-t", session])
        return True
    except subprocess.CalledProcessError:
        warn("无法附加 tmux (非 TTY 环境)")
        print(f"  手动: tmux attach -t {session}")
        return False


class StageValidator:
    """阶段完成校验器"""

    @staticmethod
    def check(task_dir: Path, stage: str, stage_idx: int) -> tuple[list[str], list[str]]:
        """返回 (done_items, todo_items)"""
        done = []
        todo = []
        tpl = task_dir / f"{stage}.md"

        if not tpl.exists():
            todo.append(f"模板文件不存在: {stage}.md")
            return done, todo

        content = tpl.read_text()

        # 统计未填充的 checkbox
        unchecked = content.count("[ ] ")
        checked = content.count("[x] ") + content.count("[X] ")

        if unchecked == 0 and checked > 0:
            done.append(f"{stage}.md — 全部 {checked} 项已勾选")
        elif unchecked > 0:
            todo.append(f"{stage}.md — {unchecked} 个待填项未完成")
        elif unchecked == 0 and checked == 0:
            # 没有 checkbox 结构，检查文件是否被修改过
            tpl_orig = TPLS / f"{stage}.md"
            if tpl_orig.exists() and content != tpl_orig.read_text():
                done.append(f"{stage}.md — 内容已修改（非初始模板）")
            else:
                todo.append(f"{stage}.md — 尚未填写（与初始模板一致）")

        # Stage-specific checks
        if stage == "01-brainstorming":
            if re.search(r'\[x\]\s*设计是否已批准', content, re.IGNORECASE):
                done.append("设计批准已勾选 [x]")
            elif '(是/否)' in content or '（是/否）' in content:
                todo.append("设计尚未获得批准（模板中仍为 '(是/否)'，需改为'是'）")
            else:
                done.append("设计批准已填写")

        elif stage == "03-coding":
            # 检查是否有 git commits
            try:
                result = subprocess.run(
                    ["git", "-C", str(ROOT), "log", "--oneline", "-5"],
                    capture_output=True, text=True)
                if result.stdout.strip():
                    done.append(f"最近 git 提交:\n    {result.stdout.strip()[:200]}")
            except Exception:
                pass

        return done, todo


def launch_tmux_stage(name: str, stage: str, stage_idx: int, agent: str) -> bool:
    """为指定 stage 启动/复用 tmux 会话: 左=Agent交互, 右=日志监控"""
    if not shutil.which("tmux"):
        warn("tmux 未安装，请手动启动:")
        _print_stage_launch_manual(name, stage, stage_idx, agent)
        return False

    session = f"sw-{name}"
    stage_name = STAGE_NAMES[stage_idx]
    log_path = f"workflow/tasks/{name}/.log"

    # 确保 .log 文件存在
    (TASKS / name / ".log").touch(exist_ok=True)

    # 检查会话是否已存在
    existing = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True)

    if existing.returncode == 0:
        # ── 复用已有会话 ──
        win = subprocess.check_output(
            ["tmux", "display", "-t", session, "-p", "#{window_index}"],
            text=True).strip()
        panes = subprocess.check_output(
            ["tmux", "list-panes", "-t", f"{session}:{win}", "-F", "#{pane_index}"],
            text=True).strip().split()
        left_pane = panes[0]
        right_pane = panes[-1] if len(panes) > 1 else panes[0]

        # 中断旧进程，清理旧后台 agent
        subprocess.run(["tmux", "send-keys", "-t", f"{session}:{win}.{right_pane}", "C-c"])
        subprocess.run(["tmux", "send-keys", "-t", f"{session}:{win}.{left_pane}", "C-c"])
        subprocess.run(["tmux", "send-keys", "-t", f"{session}:{win}.{left_pane}",
                       "kill %1 2>/dev/null; true", "Enter"])
        subprocess.run(["tmux", "send-keys", "-t", f"{session}:{win}.{right_pane}", "clear", "Enter"])
    else:
        # ── 创建新会话 ──
        subprocess.run(["tmux", "new-session", "-d", "-s", session,
                        "-c", str(ROOT)], check=True)
        subprocess.run(["tmux", "rename-window", "-t", session, "sw"], check=True)
        win = subprocess.check_output(
            ["tmux", "display", "-t", session, "-p", "#{window_index}"],
            text=True).strip()
        left_pane = subprocess.check_output(
            ["tmux", "display", "-t", session, "-p", "#{pane_index}"],
            text=True).strip()
        right_pane = subprocess.check_output(
            ["tmux", "split-window", "-h", "-P", "-F", "#{pane_index}",
             "-t", session], text=True).strip()

    # ── 右侧 pane: 监控面板（上下文头 + tail -f .log）──
    _setup_monitor(session, win, right_pane, name, stage, stage_idx, log_path)

    # ── 左侧 pane: 启动 Agent（带 tee 双写）──
    _launch_agent_left(session, win, left_pane, name, stage, stage_idx, agent, log_path)

    # ── 选中左侧 pane（用户交互区）──
    subprocess.run(["tmux", "select-pane", "-t", f"{session}:{win}.{left_pane}"])

    # ── 附加 ──
    if not os.environ.get("TMUX"):
        ok(f"tmux 会话: {session} ({stage_name})")
        os.execlp("tmux", "tmux", "attach", "-t", session)
    else:
        warn(f"切换到: {session}")
        subprocess.run(["tmux", "switch-client", "-t", session])
    return True


def _setup_monitor(session, win, pane, name, stage, stage_idx, log_path):
    """右侧 pane: 上下文头 + tail -f 日志流"""
    stage_name = STAGE_NAMES[stage_idx]
    prev = STAGES[stage_idx - 1] if stage_idx > 0 else None
    W = 54

    def send(cmd):
        subprocess.run(["tmux", "send-keys", "-t",
                       f"{session}:{win}.{pane}", cmd, "Enter"])

    send("clear")
    send(f"export SW_TASK={name}")
    send(f"export SW_STAGE={stage}")

    # 上下文头
    bar = "═" * W
    send(f"echo '{blue(bar)}'")
    title = f"  {name} · {stage} ({stage_name})  "
    send(f"echo '{blue(title.center(W))}'")
    send(f"echo '{blue(bar)}'")

    if prev:
        send(f"echo '  前序: workflow/tasks/{name}/{prev}.md'")
    send(f"echo '  模板: workflow/tasks/{name}/{stage}.md'")
    send(f"echo '  Hooks: hooks/{stage}.md'")
    send(f"echo '  日志: {log_path}'")

    send(f"echo '{blue('─' * W)}'")
    send(f"echo '  {yellow('▸')} 完成后: ./sw advance --name={name}'")
    send(f"echo '{blue('─' * W)}'")
    send("echo ''")

    # 启动 tail -f 监控
    send(f"tail -f {log_path}")


def _launch_agent_left(session, win, pane, name, stage, stage_idx, agent, log_path):
    """左侧 pane: 纯 bash + 后台启动 Agent（stdin 从 tail -f .input 管道读取）"""
    stage_name = STAGE_NAMES[stage_idx]
    input_path = f"workflow/tasks/{name}/.input"

    def send(cmd):
        subprocess.run(["tmux", "send-keys", "-t",
                       f"{session}:{win}.{pane}", cmd, "Enter"])

    # 确保 .input 文件存在
    (TASKS / name / ".input").touch(exist_ok=True)

    send("clear")
    send(f"export SW_TASK={name}")
    send(f"export SW_STAGE={stage}")
    send(f"export SW_STAGE_NAME={stage_name}")

    send(f"echo '{blue('━' * 48)}'")
    send(f"echo '  {name} · {stage_name}'")
    send(f"echo '  监控: 右侧 pane (tail -f .log)'")
    send(f"echo '{blue('━' * 48)}'")
    send("echo ''")

    if agent and agent not in ("N/A", ""):
        # 后台启动 Agent: cat 读已有内容 + tail -f 追尾
        agent_cmd = f"(cat {input_path}; tail -f {input_path}) | {agent} 2>&1 | tee -a {log_path} &"
        send(agent_cmd)
        send("echo 'Agent 已后台启动'")
        send(f"echo '回复 Agent: sw answer --name={name} --text=\"...\" '")
        send(f"echo '或直接: echo \"...\" >> {input_path}'")
        send("echo ''")
    else:
        send("echo '🤖 未指定 Agent, 无法启动'")
        send("echo '  手动: sw next --agent=opencode'")
        send("echo ''")


def _print_stage_launch_manual(name, stage, stage_idx, agent):
    """非 tmux 环境的手动启动提示"""
    stage_name = STAGE_NAMES[stage_idx]
    input_path = f"workflow/tasks/{name}/.input"
    log_path = f"workflow/tasks/{name}/.log"
    print(f"\n手动启动 {stage_name}阶段:")
    print(f"  cd {ROOT}")
    print(f"  export SW_TASK={name}")
    print(f"  export SW_STAGE={stage}")
    print(f"  export SW_STAGE_NAME={stage_name}")
    print(f"  touch {input_path} {log_path}")
    if agent:
        print(f"  # 终端1: 启动监控")
        print(f"  tail -f {log_path}")
        print(f"  # 终端2: 启动后台 Agent")
        print(f"  (cat {input_path}; tail -f {input_path}) | {agent} 2>&1 | tee -a {log_path} &")
        print(f"  # 回复 Agent 提问:")
        print(f"  ./sw answer --name={name} --text=\"你的回答\"")
    print(f"\n完成后: ./sw advance --name={name}")


# ── commands ──

def cmd_init(args):
    """创建新任务"""
    task_type = args.type or "feature"
    name = args.name or ""
    session = args.session or ""
    agent = getattr(args, "agent", "") or ""
    context = getattr(args, "context", "") or ""
    no_tmux = getattr(args, "no_tmux", False)
    interactive = getattr(args, "interactive", False)

    # 交互模式：无参数或 --interactive
    if interactive or not any([args.name]):
        interactive = True
        hdr("创建新任务 (交互模式)")
        while not name:
            name = prompt("任务名称 (必填)")
        task_type = prompt("任务类型 (feature/bugfix/refactor/chore)", task_type)
        session = prompt("Session ID", "N/A")
        agent = prompt("AI Agent (opencode/claude/gemini/回车跳过)", "")
        if not context:
            print(f"  {blue('→')} 需求描述 (输入完成后回车，再按 Ctrl+D 结束):")
            lines = []
            try:
                while True:
                    line = input()
                    lines.append(line)
            except EOFError:
                pass
            context = "\n".join(lines)
        if not no_tmux and sys.stdin.isatty():
            if not prompt_yn("创建 tmux 会话?", "y"):
                no_tmux = True

    if not name:
        die("缺少 --name")

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

    # 保存需求上下文
    if context:
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
    print("  下一步: hooks/01-brainstorming.md → templates/01-brainstorming.md")
    print(f"  完成后: ./sw advance --name={name}")

    # Tmux
    if not no_tmux:
        launch_tmux_stage(name, "01-brainstorming", 0, agent or "")


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
    """为当前 stage 启动 AI Agent（复用/创建 tmux + 注入上下文）"""
    name = getattr(args, "name", "") or ""
    agent_override = getattr(args, "agent", "") or ""
    no_tmux = getattr(args, "no_tmux", False)

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
    print(f"  强制规则: hooks/{cur_stage}.md")
    if agent:
        print(f"  Agent: {agent}")

    # 确认
    if sys.stdin.isatty():
        if not prompt_yn("确认启动?", "y"):
            print("  已取消")
            return

    # 更新状态为 in_progress
    st["stage_status"] = "in_progress"
    st["updated_at"] = now()
    write_state(name, st)

    # ── 注入上下文到 .input ──
    input_file = TASKS / name / ".input"
    context_file = TASKS / name / ".context"
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

    # 启动 tmux
    if not no_tmux:
        launch_tmux_stage(name, cur_stage, idx, agent)

    print(f"\n完成后执行: ./sw advance --name={name}")


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


def cmd_usage():
    """打印帮助信息"""
    print(__doc__)


# ── main ──

def main():
    # 无参数 → usage
    if len(sys.argv) < 2:
        cmd_usage()
        sys.exit(1)

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    # 手动解析参数（兼容 --key=value 和 --key value）
    def parse_args(argv, specs):
        result = {}
        i = 0
        while i < len(argv):
            a = argv[i]
            if "=" in a and a.startswith("--"):
                k, v = a.split("=", 1)
                k = k[2:].replace("-", "_")
                result[k] = v
            elif a in specs:
                k = a[2:].replace("-", "_")
                spec = specs[a]
                if spec.get("type") == bool:
                    result[k] = True
                elif i + 1 < len(argv) and not argv[i+1].startswith("--"):
                    i += 1
                    result[k] = argv[i]
            else:
                # positional / unknown
                pass
            i += 1
        return result

    # 简单路由
    if cmd in ("-h", "--help", "help"):
        cmd_usage()
        return

    if cmd == "init":
        from types import SimpleNamespace
        flags = {"--type": {}, "--name": {}, "--session": {}, "--agent": {},
                 "--context": {}, "--no-tmux": {"type": bool}, "--interactive": {"type": bool}}
        args = parse_args(rest, flags)
        args = SimpleNamespace(
            type=args.get("type", ""),
            name=args.get("name", ""),
            session=args.get("session", ""),
            agent=args.get("agent", ""),
            context=args.get("context", ""),
            no_tmux=args.get("no_tmux", False),
            interactive=args.get("interactive", False),
        )
        # 如果没传任何参数，自动进入交互模式
        if not rest:
            args.interactive = True
        cmd_init(args)

    elif cmd == "status":
        from types import SimpleNamespace
        flags = {"--name": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(name=kwargs.get("name", ""))
        cmd_status(args)

    elif cmd == "advance":
        from types import SimpleNamespace
        flags = {"--name": {}, "--force": {"type": bool}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(
            name=kwargs.get("name", ""),
            force=kwargs.get("force", False))
        cmd_advance(args)

    elif cmd == "next":
        from types import SimpleNamespace
        flags = {"--name": {}, "--agent": {}, "--no-tmux": {"type": bool}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(
            name=kwargs.get("name", ""),
            agent=kwargs.get("agent", ""),
            no_tmux=kwargs.get("no_tmux", False))
        cmd_next(args)

    elif cmd == "resume":
        from types import SimpleNamespace
        flags = {"--name": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(name=kwargs.get("name", ""))
        cmd_resume(args)

    elif cmd == "list":
        from types import SimpleNamespace
        flags = {"--trash": {"type": bool}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(trash=kwargs.get("trash", False))
        cmd_list(args)

    elif cmd == "remove":
        from types import SimpleNamespace
        flags = {"--name": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(name=kwargs.get("name", ""))
        cmd_remove(args)

    elif cmd == "restore":
        from types import SimpleNamespace
        flags = {"--name": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(name=kwargs.get("name", ""))
        cmd_restore(args)

    elif cmd == "answer":
        from types import SimpleNamespace
        flags = {"--name": {}, "--text": {}}
        kwargs = parse_args(rest, flags)
        args = SimpleNamespace(
            name=kwargs.get("name", ""),
            text=kwargs.get("text", ""))
        cmd_answer(args)

    else:
        cmd_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
