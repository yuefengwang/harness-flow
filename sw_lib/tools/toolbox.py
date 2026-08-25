"""
sw_lib.tools — AI Agent 插件化工具系统。

该模块将原本硬编码在 Toolbox 中的方法解耦为独立的工具类，支持：
1. 声明式工具定义 (BaseTool)。
2. 基于阶段 (Stage) 的权限过滤。
3. 更好的异常处理和安全沙箱检查。
"""

import os
import shlex
import subprocess
import queue
from pathlib import Path
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, Set

from ..core.config import (ROOT, WORKSPACE, ask_user_timeout,
                           get_tools_for_stage)


# ── 命令白名单 ──
# run_command 仅允许执行白名单内的可执行文件（首 token 匹配）。
#
# **白名单不是安全边界，它是误操作护栏。** 这个定性很重要，否则后人会基于
# 「有白名单」的错觉做设计。理由：`python`/`node` 等解释器必须保留（跑测试
# 需要），而 `python -c "open('.state','w')"` 无法用命令名堵住。真正的兜底是
# `core/evidence.py` 的 HMAC 校验 —— 篡改可检出，而非篡改不可为。
#
# `sh`/`bash`/`zsh` 已移除：实测 `sh -c '<任意命令>'` 可执行白名单外程序
# （/bin/rm 可达），因为白名单只看首 token，一层 -c 就完全绕过。
DEFAULT_ALLOWED_COMMANDS: Set[str] = {
    # 版本控制
    "git", "git-lfs",
    # Python 生态
    "python", "python3", "pip", "pip3",
    # JS/前端
    "node", "npm", "npx", "yarn", "pnpm",
    # JVM
    "mvn", "gradle", "java", "javac",
    # Go / Rust
    "go", "cargo", "rustc",
    # 构建
    "make", "cmake",
    # 容器 / 编排
    "docker", "docker-compose", "kubectl", "helm",
    # 常用 CLI 工具
    "cat", "ls", "pwd", "echo", "mkdir", "cp", "mv", "head", "tail",
    "grep", "sed", "awk", "find", "tar", "unzip", "jq", "wc", "sort", "uniq",
    "curl", "wget", "lsof", "ps", "kill", "which", "touch", "chmod", "date",
    # 测试
    "pytest", "unittest",
}


# ── 受保护路径（D0-7，修 2.4 第 1 条）──
#
# 改动要点：从「匹配 target.name」改为「匹配解析后的绝对路径前缀」。
# 旧写法只看文件名，实测 `facts/tests.json` 与 `evidence/x.log` 均可写入 ——
# 而这两处正是 A3 事实包与证据的落点，能写就等于能伪造判据。
#
# 分工：**代码写 `repo/`，判据存 `workspace/`**。因此整片保护 workspace 不会
# 堵死 coding 阶段（阶段文档 `03-coding.md` 由 harness 自己写，见
# workflow/base.py:484，不经 agent 的 write_file）。

def _protected_dirs() -> tuple:
    """返回受保护的目录/文件绝对路径。

    延迟到调用时求值：ROOT/WORKSPACE 在测试里可能被 monkeypatch。
    """
    from ..core.config import CONFIG_DIR
    return (
        WORKSPACE.resolve(),                       # .state / STATUS.json / facts / evidence
        (CONFIG_DIR / ".evidence_key").resolve(),  # 签名密钥：可读即 HMAC 形同虚设
    )


def _secret_paths() -> tuple:
    """只读也不许的路径。

    与 `_protected_dirs` 刻意分开：`.state` / `facts/` **允许读**（agent 需要
    了解自己所处阶段，禁读会让工作流不可用），但签名密钥**读到就等于能伪造**，
    因此读写皆禁。禁读的范围必须尽可能小，否则会被当噪音关掉。
    """
    from ..core.config import CONFIG_DIR
    return ((CONFIG_DIR / ".evidence_key").resolve(),)


def _is_secret(target: Path) -> bool:
    try:
        resolved = target.resolve()
    except OSError:
        return True
    return any(resolved == p for p in _secret_paths())


# harness 自己的状态文件名。除了前缀匹配，再按名字禁一层：
# 任务目录可能被搬迁到 workspace 之外，且 agent 没有任何正当理由创建同名文件。
_PROTECTED_NAMES = (".state", ".state.lock", "STATUS.json")


def _is_protected(target: Path) -> bool:
    """target 是否落在受保护范围内（含其自身与任意层级子路径）。"""
    try:
        resolved = target.resolve()
    except OSError:
        return True  # 解析不了就按最严处理，不放行
    if resolved.name in _PROTECTED_NAMES:
        return True
    for guarded in _protected_dirs():
        if resolved == guarded or guarded in resolved.parents:
            return True
    return False


class BaseTool(ABC):
    """
    Agent 工具基类。所有新工具必须继承此类。
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一标识名，供 LLM 调用"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，指导 LLM 何时及如何使用"""
        pass

    @abstractmethod
    def __call__(self, **kwargs: Any) -> Any:
        """执行工具逻辑的入口"""
        pass

    def _safe_path(self, path: str) -> Path:
        """安全路径检查，确保不越权访问项目根目录外的内容。

        使用真实路径（resolve 后）比较前缀，避免 `..` / 符号链接 / 大小写
        造成的越权逃逸。
        """
        root_resolved = ROOT.resolve()
        target = (root_resolved / path).resolve()
        if target != root_resolved and root_resolved not in target.parents:
            raise PermissionError(
                f"禁止访问项目根目录以外的路径: {path}"
            )
        return target


class ListFilesTool(BaseTool):
    @property
    def name(self) -> str: return "list_files"
    
    @property
    def description(self) -> str: return "列出指定目录下的文件列表。参数: path (str, 可选)。"

    def __call__(self, path: str = ".") -> str:
        try:
            target = self._safe_path(path)
            items = os.listdir(target)
            return "\n".join(items)
        except Exception as e:
            return f"错误: {e}"


class ReadFileTool(BaseTool):
    @property
    def name(self) -> str: return "read_file"
    
    @property
    def description(self) -> str: return "读取指定文件的全文内容。参数: file_path (str)。"

    def __call__(self, file_path: str) -> str:
        try:
            target = self._safe_path(file_path)
            # 签名密钥可读 = HMAC 形同虚设。D0-5 已阻止它随环境下传，
            # 这里补上文件读取这条路。
            if _is_secret(target):
                return f"错误: 禁止读取受保护的密钥文件 {file_path}。"
            return target.read_text(encoding="utf-8")
        except Exception as e:
            return f"错误: {e}"


class WriteFileTool(BaseTool):
    @property
    def name(self) -> str: return "write_file"
    
    @property
    def description(self) -> str: return "写入或覆盖文件内容。参数: file_path (str), content (str)。"

    def __call__(self, file_path: str, content: str) -> str:
        try:
            target = self._safe_path(file_path)
            # 判据区一律禁写（前缀匹配，覆盖 .state / STATUS.json /
            # facts/ / evidence/ 以及任意层级子路径）。
            if _is_protected(target):
                return (f"错误: 禁止写入受保护路径 {file_path}（判据区只能由 "
                        f"harness 写入）。代码请写到 repo/ 下，阶段推进用 /advance。")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"成功写入 {file_path}"
        except Exception as e:
            return f"错误: {e}"


class RunCommandTool(BaseTool):
    # 单次输出最大字符数（防止 Agent 上下文被冲垮）
    MAX_OUTPUT_CHARS = 5000

    # 允许覆盖/扩展的白名单（类级，便于测试与未来容器化映射）
    allowed_commands: Set[str] = DEFAULT_ALLOWED_COMMANDS

    @property
    def name(self) -> str: return "run_command"
    
    @property
    def description(self) -> str: return (
        "执行 Shell 命令（仅限白名单命令）。参数: command (str), cwd (str, 可选, "
        "工作目录, 须在项目根内), timeout (int, 可选, 超时秒数, 默认 300)。"
    )

    def _check_whitelist(self, command: str) -> Optional[str]:
        """校验命令首 token 在白名单内。返回 None 表示通过，否则返回错误文案。"""
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return f"错误: 命令解析失败: {e}"
        if not parts:
            return "错误: 空命令。"
        exe = Path(parts[0]).name  # 取 basename，屏蔽路径前缀绕过（如 /bin/../../usr/bin/rm）
        if exe not in self.allowed_commands:
            return (
                f"错误: 命令 '{exe}' 不在允许的白名单内。"
                f"允许的命令: {', '.join(sorted(self.allowed_commands))}"
            )
        return None

    def _check_argv_paths(self, command: str,
                          cwd: Path) -> Optional[str]:
        """检查 argv 中形似路径的参数是否指向判据区。

        判定「形似路径」的规则：含 `/`、或以 `.` 开头、或是已存在的文件名。
        相对路径按 `cwd` 解析 —— 按 ROOT 解析会漏掉 `cd` 到任务目录后的
        `sed -i s/a/b/ .state`。

        **这是护栏而非边界**：`python -c "..."` 里的路径不在 argv 里，堵不住。
        兜底仍是 evidence.py 的 HMAC 校验。
        """
        try:
            parts = shlex.split(command)
        except ValueError:
            return None  # 解析失败已由 _check_whitelist 拦下
        for arg in parts[1:]:
            if not arg or arg.startswith("-"):
                continue
            looks_like_path = ("/" in arg or arg.startswith(".")
                               or (cwd / arg).exists())
            if not looks_like_path:
                continue
            candidate = Path(arg)
            if not candidate.is_absolute():
                candidate = cwd / candidate
            if _is_protected(candidate) or _is_secret(candidate):
                return (f"错误: 禁止通过命令行访问受保护路径 {arg}"
                        f"（判据区只能由 harness 写入）。阶段推进用 /advance。")
        return None

    def __call__(self, command: str, cwd: Optional[str] = None,
                 timeout: Optional[int] = None, **_ignored: Any) -> str:
        """执行白名单内的命令。

        **`restricted` 参数已移除**（D0-7，修 2.4 第 4 条）：它曾出现在
        description 里，是 LLM 可见入参，传 `False` 即关闭状态文件保护 ——
        判据不能由被判者开关。`**_ignored` 只为兼容旧调用方仍传该参数的情形，
        传进来也不再有任何效果。
        """
        try:
            deny = self._check_whitelist(command)
            if deny:
                return deny

            exec_timeout = timeout or 300

            # cwd 必须落在 ROOT 内（修 2.4 第 6 条：实测 cwd="/Users/yfwang"
            # 可成功列出家目录）。
            if cwd:
                try:
                    cwd_resolved = self._safe_path(cwd)
                except PermissionError as e:
                    return f"错误: {e}"
                cwd_str = str(cwd_resolved)
            else:
                cwd_resolved = ROOT.resolve()
                cwd_str = str(ROOT)

            # 判据区保护：逐个检查 argv 中的路径参数（修 2.4 第 2 条）。
            # 旧实现做 `".state" in command` 字符串匹配，既漏（chr(46)+'state'
            # 可绕）又误拦（`git commit -m "fix .state parsing"` 被拒）。
            deny = self._check_argv_paths(command, cwd_resolved)
            if deny:
                return deny

            # 以参数列表方式执行，避免 shell 注入
            argv = shlex.split(command)
            res = subprocess.run(
                argv, shell=False, capture_output=True,
                text=True, cwd=cwd_str, timeout=exec_timeout
            )
            output = f"Exit Code: {res.returncode}\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
            if len(output) > self.MAX_OUTPUT_CHARS:
                output = output[:self.MAX_OUTPUT_CHARS] + "\n... (输出已截断)"
            return output
        except subprocess.TimeoutExpired:
            return "错误: 命令执行超时。"
        except FileNotFoundError as e:
            return f"错误: 命令不存在: {e}"
        except Exception as e:
            return f"错误: {e}"


class AskUserTool(BaseTool):
    def __init__(self, callbacks: Dict[str, Callable], stage: str):
        self.callbacks = callbacks
        self.stage = stage

    @property
    def name(self) -> str: return "ask_user"
    
    @property
    def description(self) -> str: return "向用户提出问题或寻求确认。参数: questions (list)。"

    def __call__(self, questions: List[Dict[str, Any]]) -> str:
        if "on_ask_user" not in self.callbacks:
            return "错误: 当前环境不支持交互式提问。"

        res_queue: queue.Queue = queue.Queue()
        # 转发给 UI (MonitorTUI)
        self.callbacks["on_ask_user"](questions, res_queue)

        try:
            # 阻塞等待 UI 收集完所有回答。上限走 `harness.ask_user_timeout`
            # （默认 30 分钟）—— 原先写死 300s，与 opencode 那条路径各有一个
            # 数字，同一个「等人多久」在两处不一致。用户调了配置却发现
            # 某条路径没变，比两处都写死更难查。
            answers = res_queue.get(timeout=ask_user_timeout())
            return self._format_answers(questions, answers)
        except queue.Empty:
            return (f"错误: 用户响应超时（已等 "
                    f"{ask_user_timeout() / 60:.0f} 分钟，无人应答）。")

    def _format_answers(self, questions: List[Dict[str, Any]], answers: List[Any]) -> str:
        res = ["用户回答如下："]
        for i, (q, a) in enumerate(zip(questions, answers)):
            res.append(f"问题 {i+1} ({q.get('question')}): {a}")
        return "\n".join(res)


class Toolbox:
    """
    Agent 工具管理器，负责根据阶段加载并提供工具实例。
    """
    
    def __init__(self, task_name: str, stage: str,
                 callbacks: Optional[Dict[str, Callable]] = None,
                 role_id: Optional[str] = None):
        self.task_name = task_name
        self.stage = stage
        self.callbacks = callbacks or {}
        self.role_id = role_id
        # A4 的 3.6：按角色解析工具白名单。
        # ⚠️ 这条路径只被 gemini 引用，对当前实际运行的 opencode 无效
        # （A0 的 2.5）—— 真实生效的是 opencode.py 的 _tool_switches。
        self.allowed_tool_names = get_tools_for_stage(stage, role_id=role_id)
        
        # 实例化工具对象
        self._all_tools: Dict[str, BaseTool] = {
            "list_files": ListFilesTool(),
            "read_file": ReadFileTool(),
            "write_file": WriteFileTool(),
            "run_command": RunCommandTool(),
            "ask_user": AskUserTool(self.callbacks, self.stage)
        }

    def get_available_tools(self) -> List[Callable]:
        """
        获取当前阶段允许的所有工具调用对象。
        返回包装后的函数列表，确保 __name__ 和 __doc__ 符合 SDK 要求。
        """
        available = []
        for name in self.allowed_tool_names:
            if name in self._all_tools:
                tool_inst = self._all_tools[name]
                
                # 使用闭包创建包装函数，以避开 bound method 不可设置 __name__ 的限制
                def create_wrapper(inst: BaseTool):
                    def tool_wrapper(**kwargs: Any) -> Any:
                        return inst(**kwargs)
                    # 设置元数据供 AI SDK (如 Gemini) 识别
                    tool_wrapper.__name__ = inst.name
                    tool_wrapper.__doc__ = inst.description
                    return tool_wrapper
                
                available.append(create_wrapper(tool_inst))
        return available
