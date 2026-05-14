#!/usr/bin/env python3
"""MonitorTUI Engine 与 Agent 生命周期测试"""
import os
import pty
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sw_lib.tui import MonitorTUI
from sw_lib.config import TASKS
from sw_lib.engine import WorkflowEngine
from sw_lib.agent import AgentManager


def _setup_task(name="test-tui-task"):
    task_dir = TASKS / name
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / ".state").write_text(
        f'name: "{name}"\nstage: "01-brainstorming"\n'
        f'stage_idx: 0\nstage_status: pending\nagent: "cat"\n'
    )
    return task_dir


def _cleanup_task(name="test-tui-task"):
    import shutil
    task_dir = TASKS / name
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)


class TestPTYBasics(unittest.TestCase):
    """PTY 底层通信机制"""

    def test_cat_echo(self):
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(["cat"], stdin=slave_fd, stdout=slave_fd,
                                stderr=slave_fd, preexec_fn=os.setsid)
        os.close(slave_fd)
        try:
            os.write(master_fd, b"hello PTY\n")
            time.sleep(0.3)
            sel = selectors.PollSelector()
            sel.register(master_fd, selectors.EVENT_READ)
            events = sel.select(timeout=2.0)
            self.assertTrue(events)
            data = os.read(master_fd, 4096)
            text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', data.decode(errors="replace"))
            self.assertIn("hello PTY", text)
        finally:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=3)
            os.close(master_fd)

    def test_multiple_writes(self):
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(["cat"], stdin=slave_fd, stdout=slave_fd,
                                stderr=slave_fd, preexec_fn=os.setsid)
        os.close(slave_fd)
        try:
            for msg in ["msg1\n", "msg2\n", "msg3\n"]:
                os.write(master_fd, msg.encode())
            time.sleep(0.5)
            data = b""
            try:
                while True:
                    chunk = os.read(master_fd, 4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"msg3" in data:
                        break
            except OSError:
                pass
            text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', data.decode(errors="replace"))
            for m in ["msg1", "msg2", "msg3"]:
                self.assertIn(m, text)
        finally:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=3)
            os.close(master_fd)

    def test_process_exit_detect(self):
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; print('bye'); time.sleep(0.2)"],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, preexec_fn=os.setsid)
        os.close(slave_fd)
        try:
            data = b""
            sel = selectors.PollSelector()
            sel.register(master_fd, selectors.EVENT_READ)
            for _ in range(30):
                events = sel.select(timeout=0.2)
                if not events:
                    if proc.poll() is not None and data:
                        break
                    continue
                try:
                    chunk = os.read(master_fd, 4096)
                    if not chunk:
                        break
                    data += chunk
                except OSError:
                    break
            try:
                sel.unregister(master_fd)
            except (KeyError, ValueError):
                pass
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.poll())
            text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', data.decode(errors="replace"))
            self.assertIn("bye", text)
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass

    def test_selectors_nonblocking(self):
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(["cat"], stdin=slave_fd, stdout=slave_fd,
                                stderr=slave_fd, preexec_fn=os.setsid)
        os.close(slave_fd)
        try:
            os.write(master_fd, b"selector test\n")
            time.sleep(0.2)
            sel = selectors.PollSelector()
            sel.register(master_fd, selectors.EVENT_READ)
            events = sel.select(timeout=1.0)
            self.assertTrue(len(events) > 0)
            data = os.read(master_fd, 4096)
            text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', data.decode(errors="replace"))
            self.assertIn("selector test", text)
            sel.unregister(master_fd)
        finally:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=3)
            os.close(master_fd)


class TestAgentLifecycle(unittest.TestCase):
    """AgentManager 直接测试（不再通过 MonitorTUI）"""

    @classmethod
    def setUpClass(cls):
        _setup_task()

    @classmethod
    def tearDownClass(cls):
        _cleanup_task()

    def test_init_no_agent(self):
        callbacks = {"add_log": lambda s, m: None, "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "")
        self.assertIsNone(agent.agent_proc)
        self.assertIsNone(agent._master_fd)

    def test_close_master_idempotent(self):
        callbacks = {"add_log": lambda s, m: None, "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "")
        agent._close_master()
        self.assertIsNone(agent._master_fd)
        m, s = os.pipe()
        agent._master_fd = m
        agent._close_master()
        self.assertIsNone(agent._master_fd)
        agent._close_master()
        self.assertIsNone(agent._master_fd)

    def test_start_agent_nonexistent(self):
        logs = []
        callbacks = {"add_log": lambda s, m: logs.append((s, m)), "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "nonexistent_xyz_999")
        agent.start()
        self.assertIsNone(agent.agent_proc)
        self.assertIsNone(agent._master_fd)
        error_logs = [msg for src, msg in logs if src == "error"]
        self.assertTrue(len(error_logs) > 0)
        self.assertIn("未找到", error_logs[0])

    def test_start_agent_cat(self):
        callbacks = {"add_log": lambda s, m: None, "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "cat")
        try:
            agent.start()
            self.assertIsNotNone(agent.agent_proc, "cat 进程应已启动")
            self.assertIsNotNone(agent._master_fd, "应设置 master_fd")
            if agent._master_fd is not None:
                try:
                    os.write(agent._master_fd, b"alive\n")
                except OSError:
                    pass
        finally:
            agent.shutdown()

    def test_send_offline_to_input_file(self):
        callbacks = {"add_log": lambda s, m: None, "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "")
        agent.send("hello offline")
        input_file = TASKS / "test-tui-task" / ".input"
        self.assertTrue(input_file.exists())
        self.assertIn("hello offline", input_file.read_text())
        input_file.unlink(missing_ok=True)

    def test_send_online_via_pty(self):
        callbacks = {"add_log": lambda s, m: None, "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "cat")
        try:
            agent.start()
            if agent.agent_proc is None:
                self.skipTest("cat 进程启动失败")
            agent.send("hello pty")
            time.sleep(0.2)
            log_file = TASKS / "test-tui-task" / ".log"
            if log_file.exists():
                content = log_file.read_text()
                self.assertIn("user", content)
        finally:
            agent.shutdown()

    def test_reader_receives_output(self):
        logs = []
        callbacks = {"add_log": lambda s, m: logs.append((s, m)), "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "cat")
        try:
            agent.start()
            if agent.agent_proc is None:
                self.skipTest("cat 进程启动失败")
            reader = threading.Thread(target=agent.reader_loop, daemon=True)
            reader.start()
            os.write(agent._master_fd, b"hello from reader\n")
            time.sleep(0.5)
            agent_logs = [msg for src, msg in logs if src == "agent"]
            self.assertTrue(len(agent_logs) > 0, f"reader 应收到输出: {logs}")
            combined = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', " ".join(agent_logs))
            self.assertIn("hello from reader", combined)
        finally:
            agent.shutdown()

    def test_inject_context(self):
        logs = []
        callbacks = {"add_log": lambda s, m: logs.append((s, m)), "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "cat")
        try:
            agent.start()
            if agent.agent_proc is None:
                self.skipTest("cat 进程启动失败")
            agent.inject_context()
            time.sleep(0.2)
            system_logs = [msg for src, msg in logs if src == "system"]
            self.assertTrue(any("上下文" in m for m in system_logs))
        finally:
            agent.shutdown()

    def test_shutdown_cleanup(self):
        callbacks = {"add_log": lambda s, m: None, "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "cat")
        agent.start()
        if agent.agent_proc is None:
            self.skipTest("cat 进程启动失败")
        agent.shutdown()
        self.assertIsNone(agent._master_fd)
        self.assertIsNone(agent.agent_proc)

    def test_restart_agent(self):
        callbacks = {"add_log": lambda s, m: None, "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "cat")
        try:
            agent.start()
            if agent.agent_proc is None:
                self.skipTest("cat 进程启动失败")
            old_pid = agent.agent_proc.pid
            agent.restart()
            if agent.agent_proc is not None:
                new_pid = agent.agent_proc.pid
                self.assertNotEqual(new_pid, old_pid, "restart 应启动新进程")
        finally:
            agent.shutdown()

    def test_restart_no_agent(self):
        callbacks = {"add_log": lambda s, m: None, "is_running": lambda: True}
        agent = AgentManager(callbacks, "test-tui-task", "01-brainstorming", 0, "")
        # agent_name="" 会让 resolve_agent_model 从 config.yaml 解析为默认模型
        # 测试 restart 不崩溃即可
        try:
            agent.restart()
        except Exception:
            pass  # 启动失败是正常的（gemini 命令可能不存在）


class TestWorkflowEngine(unittest.TestCase):
    """WorkflowEngine 编排测试"""

    @classmethod
    def setUpClass(cls):
        _setup_task()

    @classmethod
    def tearDownClass(cls):
        _cleanup_task()

    def test_build_context_with_template(self):
        """测试上下文构建包含模板或 hooks 内容"""
        callbacks = {"add_log": lambda s, m: None, "is_running": lambda: True}
        engine = WorkflowEngine("test-tui-task", "01-brainstorming", 0, "", callbacks)
        ctx = engine._build_context()
        # 应包含当前阶段模板、hooks 规则或需求上下文中的至少一个
        if ctx:
            self.assertTrue(
                "头脑风暴" in ctx or "Brainstorming" in ctx or "hook" in ctx.lower() or "强制规则" in ctx,
                f"上下文应包含阶段相关内容, 实际: {ctx[:200]}"
            )

    def test_command_dispatch(self):
        """测试 / 命令通过 engine 分发"""
        logs = []
        callbacks = {"add_log": lambda s, m: logs.append((s, m)), "is_running": lambda: True}
        engine = WorkflowEngine("test-tui-task", "01-brainstorming", 0, "", callbacks)
        engine.handle_command("status")
        sw_logs = [msg for src, msg in logs if src == "sw"]
        self.assertTrue(any("stage=" in m for m in sw_logs))

    def test_unknown_command(self):
        """测试未知命令"""
        logs = []
        callbacks = {"add_log": lambda s, m: logs.append((s, m)), "is_running": lambda: True}
        engine = WorkflowEngine("test-tui-task", "01-brainstorming", 0, "", callbacks)
        engine.handle_command("xyz")
        sw_logs = [msg for src, msg in logs if src == "sw"]
        self.assertTrue(any("未知命令" in m for m in sw_logs))


class TestTUIDispatch(unittest.TestCase):
    """MonitorTUI 输入分发（通过 Engine）"""

    @classmethod
    def setUpClass(cls):
        _setup_task()

    @classmethod
    def tearDownClass(cls):
        _cleanup_task()

    def test_quit(self):
        tui = MonitorTUI("test-tui-task", "01-brainstorming", 0, "")
        tui._dispatch("/q")
        self.assertFalse(tui.running)

    def test_empty_command(self):
        tui = MonitorTUI("test-tui-task", "01-brainstorming", 0, "")
        tui._dispatch("")
        tui._dispatch("   ")
        self.assertEqual(len(tui.log_lines), 0)

    def test_unknown_command(self):
        tui = MonitorTUI("test-tui-task", "01-brainstorming", 0, "")
        tui._dispatch("/xyz")
        sw_logs = [msg for src, msg in tui.log_lines if src == "sw"]
        self.assertTrue(any("未知命令" in m for m in sw_logs))

    def test_status_command(self):
        tui = MonitorTUI("test-tui-task", "01-brainstorming", 0, "")
        tui._dispatch("/status")
        sw_logs = [msg for src, msg in tui.log_lines if src == "sw"]
        self.assertTrue(any("stage=" in m for m in sw_logs))

    def test_context_command(self):
        tui = MonitorTUI("test-tui-task", "01-brainstorming", 0, "")
        tui._dispatch("/context")
        all_logs = [msg for src, msg in tui.log_lines]
        self.assertTrue(any("上下文" in m for m in all_logs))

    def test_non_command_input_routes_advance(self):
        """非 / 开头的输入应触发 /advance"""
        tui = MonitorTUI("test-tui-task", "01-brainstorming", 0, "")
        tui._dispatch("hello world")
        sw_logs = [msg for src, msg in tui.log_lines if src == "user"]
        self.assertTrue(any("hello world" in m for m in sw_logs))


class TestANSIStripping(unittest.TestCase):
    """ANSI 转义序列去除"""

    def test_csi_sequences(self):
        raw = "\x1b[31mhello\x1b[0m \x1b[1;32mOK\x1b[0m"
        result = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', raw)
        result = re.sub(r'\x1b\[.*?[a-zA-Z]', '', result)
        self.assertNotIn('\x1b', result)

    def test_normal_text_preserved(self):
        text = "hello world 123"
        result = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
        result = re.sub(r'\x1b\[.*?[a-zA-Z]', '', result)
        self.assertEqual(result, text)

    def test_osc_sequences(self):
        text = "\x1b]0;title\x07normal text"
        result = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
        result = re.sub(r'\x1b\].*?\x07', '', result)
        result = re.sub(r'\x1b\[.*?[a-zA-Z]', '', result)
        self.assertIn("normal text", result)
        self.assertNotIn('\x1b', result)

    def test_carriage_return(self):
        text = "line1\r\nline2\r\n"
        result = text.replace('\r\n', '\n')
        self.assertEqual(result, "line1\nline2\n")
        self.assertNotIn('\r', result)


class TestOptionExtraction(unittest.TestCase):
    """选项提取逻辑测试"""

    def test_chinese_option_format(self):
        from sw_lib.tui import extract_options
        lines = [
            ("agent", "- ⭐ **选项 A: 独立脚本** — 最直接"),
            ("agent", "- 选项 B: 集成到 FastAPI — 可复用"),
            ("agent", "- 选项 C: 两者兼顾 — 灵活"),
        ]
        opts = extract_options(lines)
        self.assertEqual(len(opts), 3)
        self.assertEqual(opts[0][0], "A")

    def test_numbered_option_format(self):
        from sw_lib.tui import extract_options
        lines = [
            ("agent", "1. 直接 print 输出到控制台"),
            ("agent", "2. 返回 JSON 格式"),
            ("agent", "3. 写入文件"),
        ]
        opts = extract_options(lines)
        self.assertEqual(len(opts), 3)
        self.assertEqual(opts[0][0], "1")
        self.assertEqual(opts[0][1], "直接 print 输出到控制台")

    def test_no_options(self):
        from sw_lib.tui import extract_options
        lines = [
            ("agent", "这是一段普通文本，没有选项"),
            ("agent", "也没有数字编号"),
        ]
        opts = extract_options(lines)
        self.assertEqual(len(opts), 0)

    def test_mixed_sources_only_agent(self):
        from sw_lib.tui import extract_options
        lines = [
            ("sw", "1. 这不是选项"),
            ("agent", "- 选项 A: 这是选项"),
        ]
        opts = extract_options(lines)
        self.assertEqual(len(opts), 1)


class TestFocusSystem(unittest.TestCase):
    """焦点切换系统测试"""

    def test_initial_focus_is_input(self):
        tui = MonitorTUI("test-focus", "01-brainstorming", 0, "")
        self.assertEqual(tui._focus, "input")

    def test_toggle_focus_with_options(self):
        tui = MonitorTUI("test-focus", "01-brainstorming", 0, "")
        tui._options = [("A", "选项A"), ("B", "选项B")]
        tui._toggle_focus()
        self.assertEqual(tui._focus, "options")
        tui._toggle_focus()
        self.assertEqual(tui._focus, "input")

    def test_toggle_focus_without_options(self):
        tui = MonitorTUI("test-focus", "01-brainstorming", 0, "")
        self.assertEqual(len(tui._options), 0)
        tui._toggle_focus()
        self.assertEqual(tui._focus, "input")

    def test_select_option_updates_log(self):
        tui = MonitorTUI("test-focus", "01-brainstorming", 0, "")
        tui._options = [("A", "独立脚本"), ("B", "集成项目")]
        tui._select_option(0)
        user_logs = [msg for src, msg in tui.log_lines if src == "user"]
        self.assertTrue(any("选择 [A]" in m for m in user_logs))
    """配置驱动工具加载测试"""

    def test_brainstorming_tools_readonly(self):
        from sw_lib.config import get_tools_for_stage
        tools = get_tools_for_stage("01-brainstorming")
        self.assertIn("list_files", tools)
        self.assertIn("read_file", tools)
        self.assertNotIn("write_file", tools)
        self.assertNotIn("run_command", tools)

    def test_coding_tools_full(self):
        from sw_lib.config import get_tools_for_stage
        tools = get_tools_for_stage("03-coding")
        self.assertIn("list_files", tools)
        self.assertIn("read_file", tools)
        self.assertIn("write_file", tools)
        self.assertIn("run_command", tools)

    def test_auto_advance_config(self):
        from sw_lib.config import is_auto_advance
        self.assertFalse(is_auto_advance())

    def test_toolbox_get_available_tools(self):
        from sw_lib.tools import Toolbox
        tb = Toolbox("test-task", "01-brainstorming")
        tools = tb.get_available_tools()
        self.assertTrue(len(tools) >= 2)
        tool_names = [t.__name__ for t in tools]
        self.assertIn("list_files", tool_names)
        self.assertIn("read_file", tool_names)
        self.assertNotIn("write_file", tool_names)

    def test_toolbox_coding_stage(self):
        from sw_lib.tools import Toolbox
        tb = Toolbox("test-task", "03-coding")
        tools = tb.get_available_tools()
        tool_names = [t.__name__ for t in tools]
        self.assertIn("write_file", tool_names)
        self.assertIn("run_command", tool_names)


if __name__ == "__main__":
    print("=" * 60)
    print("MonitorTUI Engine 与 Agent 通信测试")
    print("=" * 60)
    unittest.main(verbosity=2)