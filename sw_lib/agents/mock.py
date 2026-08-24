"""sw_lib.mock_agent — Mock Agent for flow testing

模拟 Agent（opencode/gemini）回复，便于在不依赖外部 Agent 时验证 flow 流程。
在 harness/config.yaml 中配置启用：
  harness:
    mock_agent:
      enabled: true
      response_delay: 2.0
      review_route: "05-Archive"  # 04-review 评审结论: 05-Archive/03-Coding/02-Planning
      responses:
        "01-brainstorming": "可选的自定义回复内容"
"""

import os
import threading
import time
import yaml
import queue
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..core.config import CONFIG_DIR, STAGES, STAGE_NAMES
from ..core.utils import sw_log
from .base import BaseAgent

# 场景脚本跑完时打进 .log 的标记。e2e driver 靠它判断「这一轮说完了」，
# 替代原先「日志静默 3 秒」的猜测 —— 那种猜法会在两行输出之间的 sleep 里
# 误判，是 e2e 三轮挂一轮的根因。标记是确定性的：每个阶段恰好一条。
SCENARIO_DONE_MARKER = "mock_agent scenario complete"


class MockAgent(BaseAgent):
    """
    高度优化的 Mock Agent — 模拟 Agent 生命周期、交互式对话和阶段推进。
    支持场景化脚本执行，能够模拟 ask_user 提问并根据回复产生差异化输出。
    """

    def __init__(self, tui_callbacks: Dict[str, Any], name: str, stage: str,
                 stage_idx: int, model_name: str = "mock",
                 role_id: Optional[str] = None):
        super().__init__(tui_callbacks, name, stage, stage_idx, model_name,
                         role_id=role_id)
        self.running = False
        self.agent_proc = None
        self._master_fd = None
        
        # 消息队列，用于接收来自 send() 的输入
        self.msg_queue: queue.Queue = queue.Queue()
        self._scenario_thread: Optional[threading.Thread] = None

        self._config = self._load_mock_config()

    def _load_mock_config(self) -> Dict[str, Any]:
        """从 config.yaml 加载 mock_agent 配置"""
        config_path = CONFIG_DIR / "config.yaml"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    return data.get("harness", {}).get("mock_agent", {})
            except Exception:
                pass
        return {}

    @property
    def response_delay(self) -> float:
        """回复节奏。``SW_MOCK_RESPONSE_DELAY`` 优先，便于 e2e 自己决定快慢。"""
        env = os.environ.get("SW_MOCK_RESPONSE_DELAY")
        if env:
            try:
                return max(0.0, float(env))
            except ValueError:
                pass
        return float(self._config.get("response_delay", 1.0))

    def _pause(self, seconds: float):
        """按 response_delay 缩放的思考停顿。

        场景脚本里原本散着写死的 ``sleep(1)`` / ``sleep(2)``，无视配置，
        e2e 一轮的下限被这些常数钉在 ~20 秒。改成随 response_delay 缩放后，
        ``SW_MOCK_RESPONSE_DELAY=0`` 就能让脚本几乎瞬时跑完，做流程回归时
        不必为"拟真打字速度"付时间。
        """
        scale = self.response_delay
        if scale <= 0:
            return
        time.sleep(seconds * min(scale, 1.0))

    @property
    def is_active(self) -> bool:
        return self.running

    # ── 生命周期 ──

    def start(self):
        """启动 Mock 场景线程"""
        self.running = True
        self.status = self.STATUS_IDLE
        sw_log(self.name, "mock_agent started (scenario mode)", "sw")
        
        # 清空队列防止旧消息干扰
        while not self.msg_queue.empty():
            self.msg_queue.get()

        self._scenario_thread = threading.Thread(target=self._run_scenario, daemon=True)
        self._scenario_thread.start()

    def send(self, text: str, is_system: bool = False):
        """将用户输入放入队列供场景线程消费"""
        if not self.running:
            return
        
        # 即使是 Mock 也要记录用户输入到日志
        if not is_system:
            sw_log(self.name, f"mock_agent received: {text[:100]}", "user")
            
        self.msg_queue.put(text)

    def shutdown(self):
        self.running = False
        self.status = self.STATUS_IDLE
        sw_log(self.name, "mock_agent shutdown", "sw")

    def restart(self):
        self.shutdown()
        self.start()

    def inject_context(self):
        self._add_log("system", "上下文已通过 mock 逻辑自动解析")

    def reader_loop(self):
        pass

    # ── 场景引擎 ──

    def _run_scenario(self):
        """主场景循环：根据阶段执行对应的模拟脚本"""
        try:
            # 1. 模拟连接延迟
            self.status = self.STATUS_CONNECTING
            time.sleep(min(self.response_delay, 0.5))
            
            if not self.running: return
            self.status = self.STATUS_ACTIVE
            
            # 2. 执行阶段逻辑
            stage_key = STAGES[self.stage_idx] if self.stage_idx < len(STAGES) else "unknown"
            
            if stage_key == "01-brainstorming":
                self._scenario_brainstorming()
            elif stage_key == "02-planning":
                self._scenario_planning()
            elif stage_key == "03-coding":
                self._scenario_coding()
            elif stage_key == "04-review":
                self._scenario_review()
            elif stage_key == "05-archive":
                self._scenario_archive()
            else:
                self._scenario_generic(stage_key)
                
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._add_log("error", f"Mock 场景执行异常: {e}")
            # 记录详细堆栈到本地日志
            sw_log(self.name, f"mock_scenario traceback:\n{tb}", "error")
        finally:
            # 3. 完成阶段
            if self.running:
                self.status = self.STATUS_IDLE
                if "on_complete" in self.callbacks:
                    self.callbacks["on_complete"]()
                # 标记必须最后写、且只在自然跑完时写：e2e 靠它数「第 N 个阶段
                # 的脚本说完了」。直接走 sw_log 而不是 _add_log —— 后者会把这
                # 行灌进 TUI 的 log_lines，被 extract_options 当成 agent 的最新
                # 消息块，干扰选项探测。
                sw_log(self.name, SCENARIO_DONE_MARKER, "sw")

    def _safe_get(self, lst: List[Any], index: int, default: Any = "默认回复") -> Any:
        """安全获取列表元素"""
        if not lst or not isinstance(lst, list) or index >= len(lst):
            return default
        return lst[index]

    def _say(self, text: str, source: str = "agent", speed: float = 1.0):
        """流式输出文本"""
        if not self.running: return

        lines = text.splitlines()
        # 模拟打字机速度。response_delay=0 时不打字，直接吐完 —— 拟真节奏对
        # 流程回归没有价值，只是让每轮 e2e 多花几秒。
        line_delay = (0.05 / speed) if len(lines) > 1 else 0
        if self.response_delay <= 0:
            line_delay = 0
        
        for line in lines:
            if not self.running: break
            self._add_log(source, line)
            if line_delay > 0:
                time.sleep(line_delay)

    def _ask(self, questions: List[Dict[str, Any]]) -> List[Any]:
        """模拟向用户提问并等待回复"""
        if "on_ask_user" not in self.callbacks:
            self._say("⚠️ 警告: 当前环境不支持 ask_user，模拟器将随机生成回答。")
            return ["选项 A"] * len(questions)

        res_queue: queue.Queue = queue.Queue()
        self.callbacks["on_ask_user"](questions, res_queue)
        
        self.status = self.STATUS_WAITING
        try:
            # 阻塞等待 TUI 收集完回答
            while self.running:
                try:
                    answers = res_queue.get(timeout=1.0)
                    self.status = self.STATUS_ACTIVE
                    if not answers:
                        self._add_log("error", "收到空的回答列表，交互异常")
                        return ["默认回复"] * len(questions)
                    return answers
                except queue.Empty:
                    continue
            return ["Agent 已关闭"] * len(questions)
        except Exception as e:
            self.status = self.STATUS_ERROR
            raise RuntimeError(f"MockAgent 等待用户回答失败: {e}")

    # ── 具体场景实现 ──

    def _scenario_brainstorming(self):
        self._say("你好！我是你的需求分析专家。我已阅读了你的任务需求。")
        self._pause(1)
        self._say("在开始设计之前，我需要确认几个关键细节：")
        
        # 模拟交互提问
        questions = [
            {
                "question": "这个模块是否需要支持多语言（国际化）？",
                "options": ["A. 需要，预留 i18n 接口", "B. 暂时不需要，仅限中文"],
                "header": "i18n",
                "type": "choice"
            }
        ]
        answers = self._ask(questions)
        if not answers or not self.running: return
        choice = self._safe_get(answers, 0)
        
        self._say(f"收到。由于你选择了 '{choice}'，我将据此制定设计方案。")
        self._pause(1)

        # 模拟第二个问题，验证状态切换
        self._say("还有一个细节：你希望使用哪种 API 风格？")
        q2 = [{
            "question": "API 风格选择：",
            "options": ["1. RESTful (标准)", "2. GraphQL (灵活)", "3. gRPC (高性能)"],
            "header": "api_style",
            "type": "choice"
        }]
        a2 = self._ask(q2)
        if not a2 or not self.running: return
        style = self._safe_get(a2, 0)
        self._say(f"好的，将采用 {style} 风格。")

        self._say("正在生成 Brainstorming 设计文档...")
        self._pause(2)
        
        output = (
            "## 🤖 AI Output\n\n"
            "### 需求分析结论\n"
            f"- **国际化支持**: {choice}\n"
            "- **核心架构**: 采用分层解耦设计\n\n"
            "### 方案建议\n"
            "建议使用标准的 Service 模式，将业务逻辑与接口层分离。\n"
        )
        self._say(output)

    def _scenario_planning(self):
        """02-planning 场景：产出规划阶段真正要交的三段。

        原产出只有三行 WBS 标题，去噪后 65 字符 —— 本轮给 01/02 补上
        「产出区必须有实质内容」的硬校验（阈值 80，实测校准）之后，e2e 立刻
        挂在这里。阈值不动：那是「放宽标准让存量变绿」（A6 的 9.3）。
        薄的是 mock —— 一个连自家门禁都过不了的驱动测不出任何东西。

        段落形态对齐 `templates/02-planning.md`：Task DAG（含 Deps/Do/Verify）、
        Test Strategy、Tech Detail。这不是为了凑字数 ——
        `fact_pack.build_plan` 正是按这几个标题关键词提取规划事实喂给设计
        审查轨，标题对不上它拿到的就是空段。

        WBS 条目刻意保持 `[ ]`：e2e 有一条判据在确认产出区里的 `[ ]` 没被
        全局替换污染，mock 自己输出 `[x]` 会让那条判据当场失效。
        """
        self._say("正在基于 Brainstorming 的结论拆解任务清单...")
        self._pause(2)

        output = (
            "## 🤖 AI Output\n\n"
            "### 任务拆解 (WBS) / Task DAG\n"
            "1. [ ] **Task 1**: 定义数据模型 | Deps: None\n"
            "   - **Do**: 按需求确定字段与约束，落成模块内的数据结构\n"
            "   - **Verify**: 导入模块，构造样例数据无异常\n"
            "2. [ ] **Task 2**: 实现核心 Service | Deps: Task 1\n"
            "   - **Do**: 基于数据模型实现业务入口函数，保持接口层与逻辑分离\n"
            "   - **Verify**: 调用入口函数，返回值符合预期\n"
            "3. [ ] **Task 3**: 编写单元测试 | Deps: Task 2\n"
            "   - **Do**: 覆盖正常路径与边界输入\n"
            "   - **Verify**: `python3 -m pytest -q` 全绿\n\n"
            "### Test Strategy\n"
            "- **Method**: unit（核心逻辑）+ manual（一次端到端手动确认）\n"
            "- **Key path**: 入口函数 → 数据模型 → 返回结果\n"
            "- **Repro script**: `python3 -m pytest -q`\n\n"
            "### Tech Detail\n"
            "- **Key types/interfaces**: 单模块导出一个纯函数入口，无全局状态\n"
            "- **Files to touch**: 实现模块、对应测试模块、README\n"
        )
        self._say(output)

    def _scenario_coding(self):
        """03-coding 场景：在 target_dir 下写出一个能跑通测试的最小项目。

        必须真的落地文件。03-coding 的硬校验会拒绝空产出（任务 T3 的空转事故），
        而 mock 是 e2e 的驱动 —— 只在对话里"说"写了代码，闸门照样会拦下来，
        并且这样 e2e 才真正覆盖到「有产出 + 测试通过 + README 齐备」的正路。
        """
        self._say("正在按规划实现代码...")
        target = self._resolve_target_dir()
        written = self._write_sample_project(target) if target else []
        if written:
            for rel in written:
                self._add_log("agent", f"  ✎ 写入 {rel}")
        else:
            self._add_log("error", "未能写入示例代码（target_dir 不可用）")

        time.sleep(min(self.response_delay, 1.0))
        files_md = "\n".join(f"- `{rel}`" for rel in written) or "- (无)"
        # 产出必须带模板要求的结构化声明段（Task / Verify cmd / Files Touched），
        # 否则 03 准出解析出来的 claims 是空的，而 mock 是 e2e 的唯一驱动 ——
        # 空 claims 与「没有 claims」在下游同义：验收 18 的 claims-vs-diff
        # 对照一次都不会触发，整条路径能一路绿到底。
        #
        # Files Touched 直接由 `written` 生成，不写死：声明一批没写过的文件
        # 比不声明更糟 —— 对照会通过，而它对照的是假数据。
        self._say(
            "## 🤖 AI Output\n\n"
            "## Task\n"
            f"`{self.name}-1`: 按 02-planning 的任务清单完成实现\n\n"
            "### 实现摘要\n"
            "按 02-planning 的任务清单完成实现，并补齐 README 与单元测试。\n\n"
            "## Red-Green\n"
            "- [x] **Red**: repro/test fails\n"
            "- [x] **Green**: fix passes\n"
            "- **Verify cmd**: `python3 -m pytest -q`\n\n"
            "## Files Touched\n"
            f"{files_md}\n\n"
            "### 产出文件\n"
            f"{files_md}\n"
        )

    def _resolve_target_dir(self) -> Optional[Path]:
        """从任务 .state 读 target_dir —— 与门禁钩子用的是同一个源。"""
        from ..core.state import read_state
        st = read_state(self.name) or {}
        raw = str(st.get("target_dir") or "").strip()
        if not raw or raw == ".":
            return None
        return Path(raw)

    def _write_sample_project(self, target: Path) -> List[str]:
        """写一个自洽的最小 Python 项目；返回写入的相对路径列表。"""
        files = {
            "mocknote.py": (
                '"""Mock 产出：最小可运行模块。"""\n\n\n'
                'def add(a, b):\n'
                '    return a + b\n'
            ),
            "test_mocknote.py": (
                'from mocknote import add\n\n\n'
                'def test_add():\n'
                '    assert add(1, 2) == 3\n'
            ),
            # README 齐备：04-review 在归档路径上会硬性要求它存在。
            "README.md": (
                f"# {self.name}\n\n"
                "Mock 模式产出的示例项目。\n\n"
                "## 用法\n\n"
                "```python\n"
                "from mocknote import add\n\n"
                "add(1, 2)\n"
                "```\n"
            ),
        }
        written: List[str] = []
        try:
            target.mkdir(parents=True, exist_ok=True)
            for rel, body in files.items():
                (target / rel).write_text(body, encoding="utf-8")
                written.append(rel)
        except OSError as e:
            sw_log(self.name, f"mock coding write failed: {e}", "error")
        return written

    # 各审查角色的关注点。mock 不做真判断，但产出必须**可区分** ——
    # 否则「两个审查者」与「一个跑两遍」在产出上无从分辨，
    # 多轨测试就失去了意义（A5 的 R3）。
    _ROLE_FLAVORS = {
        "adversary": "反例导向：只提可执行的反例，不写评语",
        "design_critic": "设计视角：只看设计质量与需求符合度",
        "reviewer": "通用审查：逻辑与实现一致性",
    }

    def role_flavored_output(self, body: str) -> str:
        """给产出打上角色标记。无 role_id 时**原样返回**。

        向后兼容是硬要求：单角色路径的既有 e2e 断言依赖具体文本，
        无条件加装饰会把它们全部弄红。
        """
        role_id = getattr(self, "role_id", None)
        if not role_id:
            return body
        flavor = self._ROLE_FLAVORS.get(role_id, "自定义审查角色")
        return f"[role: {role_id}] {flavor}\n\n{body}"

    def _scenario_review(self):
        """04-review 专用场景：模拟代码审查并输出 Route 决策。

        路由取值优先级：``SW_MOCK_REVIEW_ROUTE`` 环境变量 > 全局配置 >
        config.yaml。环境变量排第一是为了让 e2e 自己决定输入 —— driver 用
        fork 子进程跑 ``sw init``，改不到父进程的 ``_manager``，而从
        config.yaml 读会让测试结论跟着谁编辑过配置文件而变（仓库里那份现在
        写的是 02-Planning，直接跑 e2e 会走返工分支）。

        可选值:
        - "05-Archive" (默认) — 评审通过，正常归档
        - "03-Coding" — 代码层问题，返工到编码阶段
        - "02-Planning" — 规划层问题，返工到规划阶段
        """
        route = self._config.get("review_route", "05-Archive")
        try:
            from ..core.config import _manager
            route = _manager.config.mock_agent.review_route
        except Exception:
            pass
        route = os.environ.get("SW_MOCK_REVIEW_ROUTE") or route
        valid_routes = {"05-Archive", "03-Coding", "02-Planning"}
        if route not in valid_routes:
            route = "05-Archive"

        route_descriptions = {
            "05-Archive": ("评审通过", "代码完整、构建通过、测试绿色、安全合规，与设计规格一致。"),
            "03-Coding": ("代码层问题", "存在未实现的计划任务、构建/测试失败、代码质量缺陷或安全漏洞，需返回编码阶段修复。"),
            "02-Planning": ("规划层问题", "架构设计存在根本性缺陷、技术选型不可行、或重要任务遗漏导致无法交付，需返回规划阶段修订。"),
        }
        conclusion, reason = route_descriptions[route]

        self._say(f"正在执行代码审查...")
        self._pause(1)
        self._say(f"审查结论: {conclusion}")
        self._say(reason)

        is_pass = (route == "05-Archive")

        output = (
            f"## 🤖 AI Output\n\n"
            f"### 审查结论\n"
            f"- 代码完整性: {'✅ 通过' if is_pass else '❌ 未通过'}\n"
            f"- 构建/测试: {'✅ 通过' if is_pass else '❌ 失败'}\n"
            f"- 安全审计: {'✅ 无风险' if is_pass else '⚠️ 存在隐患'}\n"
            f"- 与设计规格一致性: {'✅ 一致' if is_pass else '❌ 存在偏差'}\n\n"
            f"**建议路由: {route}（{conclusion}）**\n"
            f"- 理由: {reason}\n\n"
        )

        if is_pass:
            output += (
                "### Reroute Evidence\n"
                "*(评审通过，无需返工)*\n"
            )
        else:
            output += (
                "### Reroute Evidence\n"
                "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
                "|---|------|---------|---------|-------------|\n"
                "| 1 | 模拟发现的问题 | high | coding | 详见审查结论 |\n"
                "| 2 | 需修复项 | med | coding | 详见审查结论 |\n"
            )

        self._say(self.role_flavored_output(output))

    def _scenario_archive(self):
        """05-archive 场景：产出 Summary / Memory / Retro 三段。

        改前归档阶段落在 `_scenario_generic`，一句「工作已顺利完成」就收工
        （去噪后 46 字符）—— 归档等于没被 e2e 覆盖过，而 `check_05-archive.sh`
        正在 grep 这三个章节（目前只 warn 不 fail，但那是钩子宽松，
        不是产出该薄的理由）。

        章节名沿用 `templates/05-archive.md` 的英文标题：归档记录会被
        `_perform_archival` 复制进 docs/history，标题是那边的检索锚点。
        """
        self._say("正在汇总本次任务的交付内容与经验...")
        self._pause(2)

        output = (
            "## 🤖 AI Output\n\n"
            "### Summary\n"
            f"- **Delivered**: 任务 `{self.name}` 的最小可运行实现，含单元测试与 README\n"
            "- **Key changes**: 新增实现模块与对应测试，补齐用法说明\n\n"
            "### Memory\n"
            "- **Learnings/pitfalls**: 阶段产出要直接写在回复正文里，"
            "记账文件由 harness 维护，agent 无权改写\n"
            "- **Patterns to promote**: 先写测试再写实现，验证命令随产出一并声明\n\n"
            "### Retro\n"
            "| Faster | Slower | Fix |\n"
            "|--------|--------|-----|\n"
            "| 需求澄清一次问清 | 环境路径确认耗时 | 启动时打印工作目录 |\n\n"
            "### Cleanup\n"
            "- 临时文件已清理，日志已归档\n"
        )
        self._say(output)

    def _scenario_generic(self, stage_key: str):
        """没有专属脚本的阶段的兜底。

        目前五个阶段都有专属场景，这条路不该再被走到 —— 保留它是为了
        「新增阶段时不至于整轮崩掉」，而不是给某个阶段当长期实现。
        """
        stage_name = STAGE_NAMES[self.stage_idx] if self.stage_idx < len(STAGE_NAMES) else stage_key
        self._say(f"正在执行 {stage_name} 阶段的自动化工作...")
        self._pause(2)
        
        output = (
            f"## 🤖 AI Output\n\n"
            f"这是 {stage_name} 阶段的模拟产出。工作已顺利完成。\n"
        )
        self._say(output)
