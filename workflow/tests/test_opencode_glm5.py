#!/usr/bin/env python3
"""测试 OpenCode CLI 访问 GLM-5 模型的连通性

验证：
1. opencode CLI 可执行且版本正确
2. opencode run --format json 可获取 NDJSON 事件流
3. -m 参数指定 GLM-5 模型可正常响应
4. 事件流包含 step_start / text / step_finish 关键事件
5. 会话延续（--continue）可正常工作
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


class TestOpenCodeGLM5CLI(unittest.TestCase):
    """OpenCode CLI + GLM-5 模型连通性测试"""

    MODEL = "opencode-go/glm-5"
    TIMEOUT = 60

    def _run_opencode(self, args, timeout=None):
        """执行 opencode run 命令，返回 (returncode, stdout, stderr)"""
        if timeout is None:
            timeout = self.TIMEOUT
        cmd = ["opencode", "run", "--format", "json", "--dangerously-skip-permissions"]
        cmd.extend(args)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                cwd=str(ROOT),
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            self.fail(f"opencode run 超时 ({timeout}s): {' '.join(cmd)}")

    def _parse_ndjson_lines(self, stdout):
        """从 stdout 解析 NDJSON 行，返回事件列表"""
        events = []
        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                events.append(obj)
            except json.JSONDecodeError:
                pass
        return events

    def test_opencode_cli_exists(self):
        """opencode 命令存在且可执行"""
        rc, out, err = self._run_opencode(["-m", self.MODEL, "回复:pong"], timeout=30)
        self.assertEqual(rc, 0, f"opencode run 返回非零退出码: {err[:300]}")

    def test_ndjson_event_flow(self):
        """事件流包含 step_start → text → step_finish 完整流程"""
        rc, out, err = self._run_opencode(["-m", self.MODEL, "回复:pong"])
        self.assertEqual(rc, 0, f"退出码非零: {err[:200]}")

        events = self._parse_ndjson_lines(out)
        event_types = [e.get("type", "") for e in events]

        self.assertIn("step_start", event_types,
                       f"缺少 step_start 事件, 实际事件: {event_types}")
        self.assertIn("text", event_types,
                       f"缺少 text 事件, 实际事件: {event_types}")
        self.assertIn("step_finish", event_types,
                       f"缺少 step_finish 事件, 实际事件: {event_types}")

    def test_step_start_has_session_id(self):
        """step_start 事件包含 sessionID"""
        rc, out, _ = self._run_opencode(["-m", self.MODEL, "回复:pong"])
        self.assertEqual(rc, 0)

        events = self._parse_ndjson_lines(out)
        start_events = [e for e in events if e.get("type") == "step_start"]

        self.assertGreater(len(start_events), 0, "未找到 step_start 事件")
        session_id = start_events[0].get("part", {}).get("sessionID", "")
        self.assertTrue(session_id.startswith("ses_"),
                        f"sessionID 格式异常: {session_id}")

    def test_text_output_contains_response(self):
        """text 事件包含模型回复内容"""
        rc, out, _ = self._run_opencode(["-m", self.MODEL, "回复词语:测试连通"])
        self.assertEqual(rc, 0)

        events = self._parse_ndjson_lines(out)
        text_parts = [e.get("part", {}).get("text", "") for e in events if e.get("type") == "text"]

        full_text = "".join(text_parts)
        self.assertTrue(len(full_text) > 0, "模型回复内容为空")

    def test_step_finish_reason(self):
        """step_finish 事件的 reason 应为 stop"""
        rc, out, _ = self._run_opencode(["-m", self.MODEL, "回复:ok"])
        self.assertEqual(rc, 0)

        events = self._parse_ndjson_lines(out)
        finish_events = [e for e in events if e.get("type") == "step_finish"]

        self.assertGreater(len(finish_events), 0, "未找到 step_finish 事件")
        reason = finish_events[0].get("part", {}).get("reason", "")
        self.assertEqual(reason, "stop", f"step_finish reason 应为 'stop', 实际: {reason}")

    def test_continue_session(self):
        """会话延续：首次消息后用 --continue 继续"""
        rc1, out1, _ = self._run_opencode(["-m", self.MODEL, "回复:第一次"])
        self.assertEqual(rc1, 0)

        events1 = self._parse_ndjson_lines(out1)
        start_events = [e for e in events1 if e.get("type") == "step_start"]
        self.assertGreater(len(start_events), 0, "首次运行缺少 step_start")

        session_id = start_events[0].get("part", {}).get("sessionID", "")
        self.assertTrue(session_id.startswith("ses_"), f"无效 sessionID: {session_id}")

        rc2, out2, _ = self._run_opencode([
            "-m", self.MODEL,
            "-c", "-s", session_id,
            "回复:第二次",
        ])
        self.assertEqual(rc2, 0)

        events2 = self._parse_ndjson_lines(out2)
        text_parts = [e.get("part", {}).get("text", "") for e in events2 if e.get("type") == "text"]
        self.assertTrue(len("".join(text_parts)) > 0, "延续会话回复为空")


if __name__ == "__main__":
    unittest.main()