"""
sw_lib.agent — Agent 进程管理与 PTY 交互。

主要内容：
1. PTYProcessor: 处理原始 PTY 字节流，解决 ANSI 转义、回退和覆盖等终端行为。
2. PtyAgent: 基于 PTY 的 Agent 实现，支持本地命令及简单的 CLI AI 工具。
"""

import fcntl
import os
import pty
import re
import selectors
import signal
import struct
import subprocess
import termios
import time
import yaml
from typing import Optional, List, Dict, Callable

from ..core.config import STAGES, STAGE_NAMES, TASKS, ROOT, CONFIG_DIR, resolve_agent_model
from ..core.utils import now, sw_log
from .base import BaseAgent


class PTYProcessor:
    """负责处理 PTY 输出流的清洗、转义序列剥离和复杂控制字符（回退、覆盖）的逻辑解析。"""

    def __init__(self):
        self.buffer: str = ""
        # 工业级强力 ANSI 清理正则：涵盖 CSI, OSC, 坐标定位, 私有序列
        self._ansi_escape = re.compile(r'''
            \x1b\[[0-?]*[ -/]*[@-~]    # CSI: 包含颜色、坐标(H)、清除、光标隐藏等
            |\x1b\][0-9];.*?\x07       # OSC: 包含窗口标题设置等
            |\x1b\][0-9];.*?\x1b\\     # OSC (alternative terminator)
            |\x1b[PX].*?\x1b\\         # DCS/SOS/PM/APC
            |\x1b[@-Z\\-_]             # 基础 2 字符转义: \x1b[ (B, \x1b=, etc.
            |[\x80-\x9F]               # C1 控制字符
        ''', re.VERBOSE)

    def process_chunk(self, data: bytes) -> List[str]:
        """
        处理从 PTY 读取的一个数据块，返回解析出的完整行列表。
        
        该方法模拟了终端的基本行为，包括：
        1. 剥离 ANSI 转义序列。
        2. 将 \r\n 归一化。
        3. 处理 \b (Backspace) 物理删除前一个字符。
        4. 处理 \r (Carriage Return) 回到行首并覆盖后续内容。
        
        Args:
            data: 原始字节数据
            
        Returns:
            清洗并解析后的完整行列表。
        """
        text = data.decode("utf-8", errors="replace")
        
        # 1. 剥离 ANSI 转义序列
        text = self._ansi_escape.sub('', text)
        
        # 2. 预处理：将 \r\n 归一化为 \n
        text = text.replace('\r\n', '\n')
        
        # 3. 处理回退符 \b 和 覆盖符 \r
        # 按字符遍历以精确模拟终端缓冲区行为
        full_text = self.buffer + text
        processed: List[str] = []
        
        for char in full_text:
            if char == '\b':
                if processed and processed[-1] != '\n':
                    processed.pop()
            elif char == '\r':
                # 回到当前行的行首：删除最后一行（直到最近的 \n）
                while processed and processed[-1] != '\n':
                    processed.pop()
            else:
                processed.append(char)
        
        current_all = "".join(processed)
        
        # 4. 提取完整的行
        lines: List[str] = []
        if '\n' in current_all:
            parts = current_all.split('\n')
            # 最后一个 part 是不完整的，留作 buffer
            for i in range(len(parts) - 1):
                line = parts[i]
                # 过滤残留的低位不可打印字符 (ASCII 0-31, 排除 \t)
                line = "".join(c for c in line if ord(c) >= 32 or c == '\t')
                line = line.strip()
                if line:
                    lines.append(line)
            self.buffer = parts[-1]
        else:
            self.buffer = current_all
                
        return lines

    def flush(self) -> Optional[str]:
        """
        强制刷新当前缓冲区中的内容，返回清洗后的残留文本。
        
        通常在超时或进程结束时调用，以获取不完整的最后一行。
        """
        res = self.buffer.strip()
        self.buffer = ""
        if res:
            return "".join(c for c in res if ord(c) >= 32 or c == '\t')
        return None


class PtyAgent(BaseAgent):
    """
    基于 PTY (Pseudo-Terminal) 的本地 Agent 实现。
    
    能够运行本地命令行 AI 工具（如 gemini-cli, opencode）或其他任意 shell 程序。
    """

    def __init__(self, tui_callbacks: Dict[str, Callable], name: str, stage: str, stage_idx: int, agent_name: str):
        super().__init__(tui_callbacks, name, stage, stage_idx, agent_name)
        self.agent_proc: Optional[subprocess.Popen] = None
        self._master_fd: Optional[int] = None
        self.processor = PTYProcessor()

    def _load_credentials(self):
        """从 workflow/harness/credentials.yaml 或 workflow/harness/config.yaml 加载凭证"""
        creds = {}
        harness_dir = CONFIG_DIR
        paths = [
            harness_dir / "credentials.yaml",
            harness_dir / "config.yaml"
        ]
        for p in paths:
            if p.exists():
                try:
                    with open(p, "r") as f:
                        data = yaml.safe_load(f)
                        if not data:
                            continue
                        if p.name == "config.yaml" and "credentials" in data:
                            creds.update(data["credentials"])
                        elif p.name == "credentials.yaml":
                            creds.update(data)
                except Exception as e:
                    self._add_log("error", f"加载凭证失败 ({p.name}): {e}")
        return creds

    def send(self, text):
        if self._master_fd is None:
            self._add_log("sw", "Agent 未运行，输入已写入 .input (sw next 后生效)")
            input_file = TASKS / self.name / ".input"
            input_file.parent.mkdir(parents=True, exist_ok=True)
            with open(input_file, "a") as f:
                f.write(f"[{now()}] user | {text}\n")
            sw_log(self.name, f"user input (offline): {text[:80]}", "user")
            return

        try:
            os.write(self._master_fd, (text + "\n").encode())
            sw_log(self.name, f"user: {text[:80]}", "user")
        except OSError:
            self._add_log("error", "Agent 连接断开")
            self._close_master()
            self.agent_proc = None

    def _close_master(self):
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    @property
    def is_active(self):
        return self._master_fd is not None

    def start(self):
        # 动态解析角色模型
        try:
            model_name = resolve_agent_model(self.stage, self.model_name)
        except Exception as e:
            self._add_log("error", f"解析角色配置失败: {e}")
            return
        
        if not model_name or model_name in ("N/A", ""):
            self._add_log("sw", "无 Agent 配置，使用 sw next 注入上下文")
            return
        
        # 拼接具体的命令
        if model_name.startswith("gemini"):
            actual_cmd = f"gemini --model {model_name}"
        else:
            # 兼容其他可能的本地程序或直接指定的命令
            actual_cmd = model_name
        
        master_fd = slave_fd = None
        try:
            self.status = self.STATUS_CONNECTING
            self._add_log("sw", f"⏳ 正在启动 Agent: {actual_cmd} ...")

            # 加载凭证并准备环境
            env = os.environ.copy()
            # 强制禁用某些 Agent 可能产生的交互式全屏特性
            env["TERM"] = "dumb"  
            env["COLUMNS"] = "80"
            env["LINES"] = "24"
            
            creds = self._load_credentials()
            if creds:
                env.update({str(k): str(v) for k, v in creds.items()})
                self._add_log("sw", f"已注入 {len(creds)} 个凭证环境变量")

            master_fd, slave_fd = pty.openpty()
            
            # 设置 PTY 窗口尺寸
            try:
                size = struct.pack("HHHH", 24, 80, 0, 0)
                fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, size)
            except:
                pass

            # 禁用回显
            try:
                attrs = termios.tcgetattr(slave_fd)
                attrs[3] = attrs[3] & ~termios.ECHO
                termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
            except:
                pass

            # 解析命令参数
            cmd_args = actual_cmd.split()

            self.agent_proc = subprocess.Popen(
                cmd_args,
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                env=env,
                cwd=str(ROOT), preexec_fn=os.setsid)
            os.close(slave_fd)
            slave_fd = None
            self._master_fd = master_fd
            self._add_log("sw", f"Agent started: {cmd_args[0]} (pid={self.agent_proc.pid})")
            
            time.sleep(1.5)
            retcode = self.agent_proc.poll()
            if retcode is not None:
                try:
                    out = os.read(self._master_fd, 4096)
                    if out:
                        for line in out.decode(errors="replace").strip().splitlines():
                            self._add_log("agent", line)
                except OSError:
                    pass
                self._add_log("error", f"Agent 退出码 {retcode}，可能启动失败。")
                self._close_master()
                self.agent_proc.wait()
                self.agent_proc = None
                return
            sw_log(self.name, f"agent started: {actual_cmd}", "sw")
        except FileNotFoundError:
            self._add_log("error", f"Agent 命令未找到: {actual_cmd}")
            if master_fd is not None: os.close(master_fd)
            if slave_fd is not None: os.close(slave_fd)
            self.agent_proc = None
        except Exception as e:
            self._add_log("error", f"Agent 启动异常: {e}")
            if master_fd is not None: os.close(master_fd)
            if slave_fd is not None: os.close(slave_fd)
            self.agent_proc = None

    def restart(self):
        if self.agent_proc:
            if self.agent_proc.poll() is None:
                try:
                    os.killpg(os.getpgid(self.agent_proc.pid), signal.SIGTERM)
                    self.agent_proc.wait(timeout=1.0)
                except Exception:
                    try:
                        self.agent_proc.kill()
                        self.agent_proc.wait()
                    except Exception:
                        pass
            else:
                self.agent_proc.wait()
            self.agent_proc = None
        self._close_master()
        self.start()
        if self.agent_proc:
            self.inject_context()

    def inject_context(self):
        if not self.agent_proc or self.agent_proc.poll() is not None:
            return
        ctx_file = TASKS / self.name / ".context"
        prev_stage = STAGES[self.stage_idx - 1] if self.stage_idx > 0 else None
        stage_name = STAGE_NAMES[self.stage_idx]

        lines = []
        lines.append(f"[系统] 当前阶段: {self.stage} ({stage_name})")
        if ctx_file.exists():
            lines.append(f"[系统] 任务需求: {ctx_file.read_text().strip()}")
        if prev_stage:
            prev_tpl = TASKS / self.name / f"{prev_stage}.md"
            if prev_tpl.exists():
                prev_content = prev_tpl.read_text()[:2000]
                lines.append(f"[系统] 前一阶段产出 ({prev_stage}):")
                for l in prev_content.splitlines()[:40]:
                    lines.append(f"  {l}")
        lines.append(f"[系统] 当前模板: {self.stage}.md")
        lines.append(f"[系统] 请开始 {stage_name} 阶段的工作。如有问题请提出选项。")

        for line in lines:
            try:
                os.write(self._master_fd, (line + "\n").encode())
                time.sleep(0.05) # 慢速注入，防止某些 Agent 缓冲区溢出
            except OSError:
                break
        self._add_log("system", f"上下文已注入 ({len(lines)} 行)")

    def reader_loop(self):
        if not self.agent_proc or self._master_fd is None:
            return
        self._add_log("sw", f"Agent reader thread started (pid={self.agent_proc.pid})")
        
        last_activity = time.time()
        log_file_path = TASKS / self.name / ".log"
        
        try:
            selector = selectors.PollSelector()
            selector.register(self._master_fd, selectors.EVENT_READ)
            
            while self._is_running():
                if self._master_fd is None:
                    break
                    
                events = selector.select(timeout=0.1)
                if not events:
                    # 交互式 Prompt 闪送机制 (基于超时刷新)
                    if time.time() - last_activity > 0.5:
                        final_part = self.processor.flush()
                        if final_part:
                            self._add_log("agent", final_part)
                            with open(log_file_path, "a") as f:
                                f.write(f"[{now()}] agent | {final_part} (flushed)\n")
                        last_activity = time.time()
                    continue

                try:
                    # 再次检查，防止 select 返回后被关闭
                    if self._master_fd is None: break
                    data = os.read(self._master_fd, 16384)
                except (OSError, TypeError):
                    break
                    
                if not data:
                    break
                
                last_activity = time.time()
                
                # 使用 PTYProcessor 处理数据块
                lines = self.processor.process_chunk(data)
                
                if lines:
                    with open(log_file_path, "a") as f:
                        for line in lines:
                            self._add_log("agent", line)
                            f.write(f"[{now()}] agent | {line}\n")
            
            # 退出前最后一次刷新
            final = self.processor.flush()
            if final:
                self._add_log("agent", final)
                
        except (ValueError, OSError) as e:
            self._add_log("error", f"Agent reader error: {e}")
        finally:
            try:
                if self._master_fd is not None:
                    selector.unregister(self._master_fd)
            except: pass
            
            retcode = self.agent_proc.poll() if self.agent_proc else -1
            if self.agent_proc and retcode is not None:
                self.agent_proc.wait()
            self._close_master()
            self.agent_proc = None
            if self._is_running():
                self._add_log("sw", f"Agent 已退出 (code={retcode})")

    def shutdown(self):
        self._close_master()
        if self.agent_proc:
            if self.agent_proc.poll() is None:
                try:
                    os.killpg(os.getpgid(self.agent_proc.pid), signal.SIGTERM)
                    self.agent_proc.wait(timeout=1.0)
                except:
                    try:
                        self.agent_proc.kill()
                        self.agent_proc.wait()
                    except: pass
            else:
                self.agent_proc.wait()
            self.agent_proc = None
