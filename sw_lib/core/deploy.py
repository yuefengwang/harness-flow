"""
sw_lib.core.deploy — 共享部署运行器

从 sw_lib/web/routes/tasks.py 提取，供 CLI 和 Web Dashboard 共用。
v2: 引入 DeployRunner 类封装 4 步部署流程 + 结构化 Agent 输出解析。
"""

import json
import os
import re
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

from ..agents.base import AgentFactory
from ..core.config import TASKS, resolve_agent_type, resolve_agent_model
from ..core.utils import now
from ..core.service import _service
from ..web.cloudflared import start_tunnel, stop_tunnel


# ── 数据类型 ──

@dataclass
class ProjectInfo:
    """项目检测结果"""
    type: str = "python"           # 语言类型
    framework: str = "generic"     # fastapi | flask | django | generic
    entry: str = ""                # 入口文件路径 (如 "main.py")
    port: int = 8000               # 确定使用的端口
    has_requirements: bool = False # 是否有 requirements.txt
    has_venv: bool = False         # 是否已有 .venv
    has_pyproject: bool = False    # 是否有 pyproject.toml
    has_setup: bool = False        # 是否有 setup.py
    has_manage: bool = False       # 是否有 manage.py (Django)


# ── 核心: DeployRunner ──

class DeployRunner:
    """
    封装一键部署的 4 步流程：

    1. detect_project — 扫描 target_dir，检测项目类型和入口文件
    2. install_deps   — 创建 venv + pip install（失败不阻塞）
    3. resolve_port   — 从 preferred 开始尝试，被占用则 +1
    4. start_service  — 根据检测结果生成启动命令，验证存活
    5. run()          — 执行完整流程，返回部署 URL
    """

    SERVICE_START_TIMEOUT = 10  # 启动后等待进程存活的最大秒数

    def __init__(
        self,
        name: str,
        target_dir: str,
        port: int = 8000,
        no_tunnel: bool = False,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.name = name
        self.target_dir = Path(target_dir)
        self.port = port
        self.no_tunnel = no_tunnel
        self.log_callback = log_callback
        self._process: Optional[subprocess.Popen] = None

    # ── 日志 ──

    def _log(self, msg: str):
        deploy_log_path = TASKS / self.name / ".deploy_log"
        deploy_log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = now()
        with open(deploy_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")
        if self.log_callback:
            self.log_callback(msg)

    # ── Step 1: 项目检测 ──

    def detect_project(self) -> ProjectInfo:
        """扫描 target_dir，返回 ProjectInfo"""
        info = ProjectInfo()
        info.port = self.port
        target = self.target_dir

        # 检测文件存在性
        info.has_requirements = (target / "requirements.txt").is_file()
        info.has_pyproject = (target / "pyproject.toml").is_file()
        info.has_setup = (target / "setup.py").is_file()
        info.has_manage = (target / "manage.py").is_file()
        info.has_venv = (target / ".venv").is_dir()

        # 检测入口文件 (优先级: main.py > app.py > manage.py)
        if (target / "main.py").is_file():
            info.entry = "main.py"
        elif (target / "app.py").is_file():
            info.entry = "app.py"
        elif info.has_manage:
            info.entry = "manage.py"
        else:
            info.entry = ""

        # 检测框架
        if info.entry == "manage.py":
            info.framework = "django"
        elif info.entry == "main.py" or info.entry == "app.py":
            # 尝试读取入口文件推断框架
            entry_path = target / info.entry
            if entry_path.is_file():
                try:
                    content = entry_path.read_text(encoding="utf-8")
                    if "fastapi" in content or "FastAPI" in content:
                        info.framework = "fastapi"
                    elif "flask" in content or "Flask" in content:
                        info.framework = "flask"
                    elif "uvicorn" in content:
                        info.framework = "fastapi"
                except Exception:
                    pass

        return info

    # ── Step 2: 安装依赖 ──

    def install_deps(self, info: ProjectInfo) -> bool:
        """
        安装 Python 依赖：
        - 若 .venv 不存在则创建
        - 若有 requirements.txt 则 pip install
        - 超时 120s，失败告警不阻塞

        Returns: True if install succeeded (or skipped), False on failure
        """
        target = self.target_dir
        venv_path = target / ".venv"
        pip = str(venv_path / "bin" / "pip")

        # 创建 venv
        if not info.has_venv:
            self._log("创建 Python 虚拟环境 (.venv)...")
            try:
                subprocess.run(
                    ["python3", "-m", "venv", str(venv_path)],
                    cwd=str(target),
                    capture_output=True,
                    timeout=30,
                    check=True,
                )
                info.has_venv = True
                self._log("虚拟环境创建成功")
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                self._log(f"创建虚拟环境失败 (将使用系统 Python): {e}")
                pip = "pip3"
        else:
            self._log("检测到已有 .venv，跳过创建")
            pip = str(venv_path / "bin" / "pip")

        # 安装 requirements.txt
        if info.has_requirements:
            self._log("安装依赖 (pip install -r requirements.txt)...")
            try:
                result = subprocess.run(
                    [pip, "install", "-r", "requirements.txt"],
                    cwd=str(target),
                    capture_output=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    self._log("依赖安装完成")
                    return True
                else:
                    self._log(f"依赖安装告警: {result.stderr.decode(errors='replace')[-200:]}")
                    return False
            except subprocess.TimeoutExpired:
                self._log("依赖安装超时 (>120s)，将继续尝试启动服务")
                return False
        else:
            self._log("未检测到 requirements.txt，跳过依赖安装")
            return True

    # ── Step 3: 端口确定 ──

    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        """检查端口是否已被占用"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) == 0

    def resolve_port(self, preferred: int) -> int:
        """
        从 preferred 端口开始尝试，被占用则 +1 重试至 preferred+10。
        全部被占用则抛出 RuntimeError。
        """
        max_port = preferred + 10
        for port in range(preferred, max_port + 1):
            if not self._is_port_in_use(port):
                if port != preferred:
                    self._log(f"端口 {preferred} 已被占用，使用端口 {port}")
                return port

        raise RuntimeError(
            f"端口 {preferred}-{max_port} 全部被占用，无法启动服务"
        )

    # ── Step 4: 启动服务 ──

    def _build_start_command(self, info: ProjectInfo) -> list[str]:
        """根据检测结果构建启动命令"""
        target = self.target_dir
        venv_path = target / ".venv"
        python = str(venv_path / "bin" / "python3") if info.has_venv else "python3"

        if info.framework == "fastapi":
            if info.entry == "main.py":
                # 尝试从 main.py 推断 app 变量名
                entry_path = target / "main.py"
                app_var = "app"
                try:
                    content = entry_path.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        m = re.match(r'\s*(\w+)\s*=\s*FastAPI\(', line)
                        if m:
                            app_var = m.group(1)
                            break
                        m = re.match(r'\s*(\w+)\s*=\s*Flask\(', line)
                        if m:
                            app_var = m.group(1)
                            break
                except Exception:
                    pass
                module = info.entry.replace(".py", "")
                return [
                    "uvicorn", f"{module}:{app_var}",
                    "--host", "0.0.0.0",
                    "--port", str(info.port),
                ]
            elif info.entry == "app.py":
                return [
                    "uvicorn", "app:app",
                    "--host", "0.0.0.0",
                    "--port", str(info.port),
                ]
        elif info.framework == "flask":
            env = os.environ.copy()
            env["FLASK_APP"] = info.entry
            env["FLASK_RUN_HOST"] = "0.0.0.0"
            env["FLASK_RUN_PORT"] = str(info.port)
            return ["flask", "run"]

        elif info.framework == "django":
            return [python, "manage.py", "runserver", f"0.0.0.0:{info.port}"]

        # 通用: python3 entry.py
        if info.entry:
            return [python, info.entry]

        # 兜底: 尝试 uvicorn main:app
        return [
            "uvicorn", "main:app",
            "--host", "0.0.0.0",
            "--port", str(info.port),
        ]

    def start_service(self, info: ProjectInfo) -> Optional[str]:
        """
        启动服务进程，等待 5s 验证存活。

        Returns:
            None if startup fails within timeout
            str: 服务本地 URL (如 http://localhost:8000)
        """
        cmd = self._build_start_command(info)
        self._log(f"启动命令: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.target_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as e:
            self._log(f"启动失败: 命令未找到 ({e})")
            return None

        self._process = proc

        # 保存 PID
        pid_file = TASKS / self.name / ".deploy.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(proc.pid))

        # 等待 5s 验证存活
        self._log("等待服务启动 (5s)...")
        deadline = time.time() + self.SERVICE_START_TIMEOUT
        startup_output = []

        while time.time() < deadline:
            ret = proc.poll()
            if ret is not None:
                # 进程已退出
                remaining = proc.stdout.read() if proc.stdout else ""
                startup_output.append(remaining)
                self._log(f"服务进程已退出 (code={ret})")
                for line in startup_output:
                    if line.strip():
                        self._log(f"  {line.strip()[-200:]}")
                return None
            time.sleep(0.5)

        # 再次检查进程存活
        if proc.poll() is not None:
            self._log("服务进程在启动后立即退出")
            return None

        # 验证端口监听
        if self._is_port_in_use(info.port):
            self._log("端口已处于监听状态，服务启动成功")
        else:
            self._log("服务进程运行中但端口尚未监听，将重试...")
            # 再等 3s
            time.sleep(3)
            if not self._is_port_in_use(info.port):
                self._log("端口仍未监听，服务可能未正常启动")
                return None

        url = f"http://localhost:{info.port}"
        self._log(f"服务已启动: {url}")
        return url

    # ── 停止服务 ──

    def stop_service(self):
        """停止已启动的服务进程"""
        pid_file = TASKS / self.name / ".deploy.pid"
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=3)
            except Exception:
                pass
        elif pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
            except (ValueError, ProcessLookupError, OSError):
                pass
        pid_file.unlink(missing_ok=True)

    # ── 完整流程 ──

    def run(self) -> str:
        """
        执行完整部署流程，返回最终 URL。

        Returns:
            部署后的服务 URL (Tunnel 优先，否则为本地地址)
        """
        self._log(f"开始部署: {self.target_dir}")

        # Step 1: 项目检测
        self._log("[Step 1/4] 检测项目类型...")
        info = self.detect_project()
        self._log(f"  → 类型: {info.type}, 框架: {info.framework}, 入口: {info.entry or '(无)'}")
        self._log(f"  → requirements.txt: {'✓' if info.has_requirements else '✗'}, "
                   f".venv: {'✓' if info.has_venv else '✗'}")

        # Step 2: 安装依赖
        self._log("[Step 2/4] 安装依赖...")
        self.install_deps(info)

        # Step 3: 确定端口
        self._log("[Step 3/4] 确定端口...")
        try:
            resolved_port = self.resolve_port(self.port)
            info.port = resolved_port
            self._log(f"  → 使用端口: {resolved_port}")
        except RuntimeError as e:
            self._log(f"  → 端口选择失败: {e}")
            try:
                _service.complete_deploy(self.name, success=False)
            except Exception:
                pass
            return ""

        # Step 4: 启动服务
        self._log("[Step 4/4] 启动服务...")
        deploy_url = self.start_service(info)
        if not deploy_url:
            self._log("服务启动失败")
            try:
                _service.complete_deploy(self.name, success=False)
            except Exception:
                pass
            return ""

        # 可选: Cloudflare Tunnel
        tunnel_url = None
        if not self.no_tunnel:
            port_match = re.search(r':(\d+)', deploy_url)
            if port_match:
                port = int(port_match.group(1))
                self._log("正在创建 Cloudflare Tunnel...")
                tunnel_url = start_tunnel(port, self.name)
                if tunnel_url:
                    self._log(f"Cloudflare Tunnel 已创建: {tunnel_url}")
                else:
                    self._log("Cloudflare Tunnel 不可用，使用本地地址")
        else:
            self._log("--no-tunnel 已指定，跳过 Cloudflare Tunnel")

        final_url = tunnel_url or deploy_url
        try:
            _service.complete_deploy(self.name, success=True, deploy_url=final_url)
        except Exception:
            pass

        self._log(f"部署完成: {final_url}")
        return final_url


# ── 兼容接口 ──

def run_deploy_agent(
    name: str,
    target_dir: str,
    log_callback: Optional[Callable[[str], None]] = None,
    port: int = 8000,
    no_tunnel: bool = False,
    use_agent: bool = True,
) -> str:
    """
    运行部署，通过 Agent 辅助检测 + DeployRunner 执行。

    Args:
        name: 任务名称
        target_dir: 目标项目目录
        log_callback: 可选的实时日志回调 (如 print)
        port: 首选端口 (默认 8000)
        no_tunnel: 是否跳过 Cloudflare Tunnel
        use_agent: 是否使用 Agent 辅助检测 (默认 False 使用 DeployRunner)

    Returns:
        部署后的服务 URL (Cloudflare Tunnel 优先，否则为本地地址)
    """
    if not use_agent:
        # 新路径: DeployRunner 直接执行
        runner = DeployRunner(
            name=name,
            target_dir=target_dir,
            port=port,
            no_tunnel=no_tunnel,
            log_callback=log_callback,
        )
        return runner.run()

    # 旧路径 (兼容): Agent 辅助检测 + DeployRunner 执行
    deploy_log_path = TASKS / name / ".deploy_log"
    agent_output_lines: list[str] = []

    def log(msg: str):
        timestamp = now()
        with open(deploy_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")
        if log_callback:
            log_callback(msg)

    try:
        log(f"开始部署 (Agent 辅助模式): {target_dir}")
        agent_type = resolve_agent_type("03-coding")
        model_name = resolve_agent_model("03-coding")
        context = (
            "## 部署任务 (Agent 辅助检测)\n\n"
            f"进入项目目录 {target_dir}，检测项目类型并输出结构化 JSON 结果。\n\n"
            "请完成以下检测：\n"
            "1. 检查是否存在 requirements.txt, pyproject.toml, setup.py\n"
            "2. 检查是否存在 .venv 目录\n"
            "3. 检测入口文件 (main.py / app.py / manage.py)\n"
            "4. 推断框架 (fastapi / flask / django / generic)\n\n"
            "请以 JSON 格式输出检测结果，格式如下：\n"
            '{"type": "python", "framework": "fastapi", "entry": "main.py", '
            '"has_requirements": true, "has_venv": false}\n\n'
            "输出 JSON 后即可结束，不需要启动服务。\n"
        )
        callbacks = {
            "add_log": lambda s, m: (log(f"[{s}] {m}"), agent_output_lines.append(m)),
            "is_running": lambda: True,
            "on_complete": lambda: None,
            "on_ask_user": lambda q, r: r.put([""] * len(q)),
        }
        agent = AgentFactory.create(agent_type, callbacks, name, "deploy", -1, model_name)
        agent.start()
        if hasattr(agent, 'send'):
            agent.send(context, is_system=True)
        from ..agents.pty import PtyAgent
        import threading as _th
        if isinstance(agent, PtyAgent):
            _th.Thread(target=agent.reader_loop, daemon=True).start()
        if hasattr(agent, 'wait'):
            agent.wait()

        # 从 Agent 输出解析 JSON
        agent_info = None
        for line in agent_output_lines:
            # 查找 JSON 块
            json_match = re.search(r'\{[^}]+\}', line)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(0))
                    if isinstance(parsed, dict) and "framework" in parsed:
                        agent_info = parsed
                        log(f"Agent 检测结果: {json.dumps(agent_info)}")
                        break
                except json.JSONDecodeError:
                    continue

        # 使用 DeployRunner 执行部署 (基于 Agent 检测结果)
        runner = DeployRunner(
            name=name,
            target_dir=target_dir,
            port=port,
            no_tunnel=no_tunnel,
            log_callback=log_callback,
        )

        # 如果 Agent 成功检测到信息，合并到 runner 的检测结果中
        info = runner.detect_project()
        if agent_info:
            if agent_info.get("framework"):
                info.framework = agent_info["framework"]
            if agent_info.get("entry"):
                info.entry = agent_info["entry"]
        info.port = port

        log(f"最终项目信息: 框架={info.framework}, 入口={info.entry}, 端口={info.port}")

        # 安装依赖
        runner.install_deps(info)

        # 确定端口
        try:
            resolved_port = runner.resolve_port(info.port)
            info.port = resolved_port
        except RuntimeError as e:
            log(f"端口选择失败: {e}")
            try:
                _service.complete_deploy(name, success=False)
            except Exception:
                pass
            return ""

        # 启动服务
        deploy_url = runner.start_service(info)
        if not deploy_url:
            log("服务启动失败")
            try:
                _service.complete_deploy(name, success=False)
            except Exception:
                pass
            return ""

        # Tunnel
        tunnel_url = None
        if not no_tunnel:
            port_match = re.search(r':(\d+)', deploy_url)
            if port_match:
                port = int(port_match.group(1))
                log("正在创建 Cloudflare Tunnel...")
                tunnel_url = start_tunnel(port, name)
                if tunnel_url:
                    log(f"Cloudflare Tunnel 已创建: {tunnel_url}")
                else:
                    log("Cloudflare Tunnel 不可用，使用本地地址")
        else:
            log("--no-tunnel 已指定，跳过 Cloudflare Tunnel")

        final_url = tunnel_url or deploy_url
        try:
            _service.complete_deploy(name, success=True, deploy_url=final_url)
        except Exception:
            pass

        log(f"部署完成: {final_url}")
        return final_url

    except Exception as e:
        log(f"部署失败: {e}")
        try:
            _service.complete_deploy(name, success=False)
        except Exception:
            pass
        return ""
