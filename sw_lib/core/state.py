"""
sw_lib.state — 任务状态持久化及汇总管理。

主要职责：
1. 维护任务的 .state 文件 (JSON 格式)，每个任务独立管理自身状态。
2. 通过扫描 STATUS.json 查找当前活跃任务。
3. 封装对任务状态的读取、写入及汇总逻辑。
"""

import json
import fcntl
import os
import contextlib
import tempfile
import time
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Iterator

from .config import TASKS, STATUS, STAGES


def state_path(name: str) -> Path:
    """获取任务 .state 文件的绝对路径"""
    return TASKS / name / ".state"


# `read_state` 用它标记「读不出来」。抽成常量而不是散落字面量 ——
# 改名时不至于漏掉某一处判定，那种漏会让损坏静默通过。
class _NoChange:
    """`update_state` 的 mutator 返回它表示「无事可做，不要写盘」。

    需要这个原语的原因：`seed_gate` 幂等、`issue_output_nonce` 在 nonce
    已存在时都不该写。若强迫它们写一次，只读操作会刷新 updated_at 并重算
    签名，审计里凭空多出大量无意义的状态变更。
    """

    def __repr__(self) -> str:      # pragma: no cover - 仅便于调试
        return "<NO_CHANGE>"


NO_CHANGE = _NoChange()

CORRUPT_FLAG = "_corrupted"


def lock_path(name: str) -> Path:
    """任务状态锁文件的路径。

    **必须与 `.state` 分离。** 原子写用 `os.replace` 替换 `.state`，
    会把它的 inode 换掉；锁若持在 `.state` 上，替换之后持锁方守着的是
    一个已被解链的旧 inode —— 互斥静默失效，且表面上毫无异常。
    """
    return TASKS / name / ".state.lock"


def _sign_on_write() -> bool:
    """证据签名是否启用。回滚开关见 A0 第 10 节（第二层）。"""
    try:
        from .config import get_harness_config
        cfg = get_harness_config()
        return bool(getattr(cfg, "evidence_sign", True))
    except Exception:
        return True


def read_state(name: str) -> Dict[str, Any]:
    """
    读取并解析任务的 .state 文件。

    只接受 JSON。解析失败时返回 `{"_corrupted": True, ...}`，**不回写、
    不填充默认阶段** —— 调用方必须显式处理。

    设计依据：docs/design/A0-state-integrity.md 的 D0-1。
    此前这里会把解析失败的内容当成旧的 `key: value` 文本格式解析并回写，
    结果是「写到一半的 .state」被当作旧格式解析成功后**固化** ——
    键名变成 `"\"id\""` 这类垃圾、`red_witness` 整棵子树丢失，
    而且因为下面的默认值填充，stage 会静默回退到 01-brainstorming。
    损坏本身尚可恢复，被伪装成一份自洽的合法状态就不可恢复了。
    旧格式迁移已移出读路径，见 scripts/migrate_state_format.py。

    Args:
        name: 任务名称

    Returns:
        状态字典。文件不存在或为空时返回空字典；
        内容无法解析时返回带 `_corrupted` 标记的字典。
    """
    sf = state_path(name)
    if not sf.exists():
        return {}
    
    try:
        content = sf.read_text(encoding="utf-8").strip()
    except Exception:
        return {}

    if not content:
        return {}

    state: Dict[str, Any] = {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        # 不回写、不填默认值：让调用方看见「读不出来」，而不是一份假状态。
        return {
            CORRUPT_FLAG: True,
            "_corrupt_reason": f"{type(e).__name__}: {e}",
            "_state_path": str(sf),
        }
    if not isinstance(parsed, dict):
        return {
            CORRUPT_FLAG: True,
            "_corrupt_reason": f"顶层不是对象，实际为 {type(parsed).__name__}",
            "_state_path": str(sf),
        }
    state = parsed

    # 核心字段类型强制转换与默认值填充
    if "stage_idx" in state:
        try:
            state["stage_idx"] = int(state["stage_idx"])
        except (ValueError, TypeError):
            state["stage_idx"] = 0
    else:
        state["stage_idx"] = 0
        
    if "stage" not in state:
        state["stage"] = STAGES[0]
    else:
        state["stage"] = state["stage"].strip('"')

    # health_config 默认值自动填充（向后兼容旧 .state 文件）
    if "health_config" not in state:
        state["health_config"] = {
            "enabled": True,
            "check_interval": 10,
            "failure_threshold": 3,
            "auto_redeploy": False,
            "max_redeploys": 5,
            "redeploy_window_sec": 300,
        }

    return state


class StateCorruptedError(ValueError):
    """`.state` 无法解析，且拒绝在此基础上继续操作。

    继承 `ValueError` 是刻意的：`write_state` 原先就抛 `ValueError`，
    既有 `except ValueError` 的调用点行为不变，不引入新的逃逸路径。
    """


def is_corrupted(state: Dict[str, Any]) -> bool:
    """状态是否为「读不出来」的三态之一。

    存在的意义是让调用方不必去猜 `_corrupted` 这个私有键名 ——
    D0-1 落地后一段时间内，35 处 `read_state` 调用点无人做这个检查，
    因为没有一个显而易见的判定入口。
    """
    return bool(isinstance(state, dict) and state.get(CORRUPT_FLAG))


def raise_if_corrupted(state: Dict[str, Any], name: str = "") -> Dict[str, Any]:
    """损坏则抛出**面向用户**的错误；否则原样返回，便于串在读之后。

    与 `write_state` 内部那道拦截的分工：
    那道是**兜底**（防止损坏被持久化，错误信息面向实现者）；
    这道是**前置**（在入口处快速失败，告诉用户哪个文件坏了、怎么修）。

    实测改前的表现：`WorkflowRuntime.advance` 一路走到 `write_state`
    才抛裸 `ValueError`，栈很深，提示是"调用方读到损坏的 .state 后仍继续
    修改并回写"—— 那是给我看的，不是给用户看的。
    """
    if not is_corrupted(state):
        return state
    path = state.get("_state_path", "<未知路径>")
    reason = state.get("_corrupt_reason", "未知原因")
    raise StateCorruptedError(
        f"任务 {name or '<未知>'} 的 .state 文件无法解析，已停止操作以免覆盖原文。\n"
        f"  文件: {path}\n"
        f"  原因: {reason}\n"
        f"  修复: 先备份该文件，再执行 "
        f"`python3 scripts/migrate_state_format.py --task {name}` 尝试迁移；"
        f"若原文已不可救，删除该文件会让任务按新建处理。"
    )


def write_state(name: str, data: Dict[str, Any]):
    """
    将状态字典以 JSON 格式**原子地**持久化到任务的 .state 文件。

    写同目录临时文件 → fsync → os.replace → fsync 父目录。
    同分区的 os.replace 是原子的，因此读者只会看到完整的旧版或完整的新版。

    设计依据：docs/design/A0-state-integrity.md 的 D0-2。
    此前直接 `open(sf, "w")` + `json.dump`：先截断再写，一旦中途失败
    （序列化异常、进程被杀、掉电），磁盘上留下半截 JSON。
    配合旧的读路径，那份半截内容会被当成旧格式解析并固化。

    Args:
        name: 任务名称
        data: 状态字典
    """
    sf = state_path(name)
    sf.parent.mkdir(parents=True, exist_ok=True)

    # 拒绝把损坏标记持久化。
    #
    # read_state 对无法解析的 .state 返回 {"_corrupted": True, ...}。
    # 全仓库有十余处 `read_state` → 改 → `write_state` 的调用点，它们拿到这份
    # 空壳后会照常修改并写回 —— 结果是把损坏内容**覆盖掉**，
    # 正好摧毁 D0-1 要保住的原始内容（实测：stage_state.seed_gate 会这样做）。
    # 逐个改调用点繁琐且必然有遗漏，因此拦在这个共同瓶颈上。
    if data.get(CORRUPT_FLAG):
        raise ValueError(
            f"拒绝写入带 _corrupted 标记的状态: {sf}。"
            f"这通常意味着调用方读到损坏的 .state 后仍继续修改并回写。"
            f"原因: {data.get('_corrupt_reason')}"
        )

    # 证据字段签名（A0 的 D0-5）。
    #
    # 放在这里而不是 update_state：write_state 是所有写路径的共同瓶颈，
    # 签名必须覆盖全部写入，否则任何一处漏签都会让该状态变成 unsigned，
    # 下游关卡拒绝通过 —— 那是能被发现的失败，但仍是失败。
    if _sign_on_write():
        from .evidence import attach_signature
        attach_signature(data)

    # 关键字段预处理，防止写入非法类型
    if "stage_idx" in data:
        try:
            data["stage_idx"] = int(data["stage_idx"])
        except (ValueError, TypeError):
            pass

    # 先完整序列化到内存：序列化失败时目标文件尚未被触碰。
    payload = json.dumps(data, ensure_ascii=False, indent=2)

    tmp_name = None
    try:
        # 必须与目标同目录 —— 跨分区的 os.replace 不是原子操作。
        fd, tmp_name = tempfile.mkstemp(prefix=".state.", suffix=".tmp",
                                        dir=str(sf.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, sf)
        tmp_name = None
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)

    # 目录项也要落盘，否则崩溃后可能看到 rename 未生效。
    try:
        dir_fd = os.open(str(sf.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        # 某些文件系统不支持对目录 fsync；替换本身已经是原子的。
        pass


# ── 受控写入入口：串行化 read-modify-write ──

@contextlib.contextmanager
def state_lock(name: str, timeout: float = 10.0) -> Iterator[None]:
    """持任务状态的排他锁。锁文件独立于 `.state`（见 lock_path）。

    `fcntl.flock` 是劝告锁：只对同样走这里的调用方有效。它防的是
    harness 自身的并发写（TUI / Web / HealthMonitor 线程 / 04 阶段多轨），
    不防绕过入口的外部进程 —— 那由 A0 第二层的签名校验兜住。
    """
    lf = lock_path(name)
    lf.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lf), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"获取状态锁超时（{timeout}s）: {lf}")
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def update_state(name: str, mutator: Callable[[Dict[str, Any]], Dict[str, Any]],
                 timeout: float = 10.0) -> Dict[str, Any]:
    """在锁保护下执行 读 → mutator → 原子写，返回落盘后的状态。

    这是**写入 `.state` 的受控入口**。A2 的 red_witness、A3 的事实包、
    A6 的客观轨结论、A9 的 Route 决策都必须走这里。

    为什么需要它：`read_state` + 改 + `write_state` 的写法在并发下会丢更新 ——
    两个调用方读到同一份旧状态，后写者覆盖前写者，且没有任何报错。
    04 阶段有多条审查轨并行写入，Web 与 TUI 可同时运行，
    HealthMonitor 还在独立线程里写。详见
    docs/design/A0-state-integrity.md 的 2.3（含实测复现）。

    对损坏的 `.state` 拒绝执行：否则 mutator 会基于 `_corrupted` 空壳
    做修改并写回，把损坏彻底覆盖，原始内容再也拿不回来。
    """
    with state_lock(name, timeout=timeout):
        state = read_state(name)
        if state.get(CORRUPT_FLAG):
            raise ValueError(
                f"拒绝在损坏的 .state 上执行写入: {state.get('_state_path')} "
                f"（{state.get('_corrupt_reason')}）。"
                f"请人工检查，或用 scripts/migrate_state_format.py 处理。"
            )
        new_state = mutator(state)
        if new_state is NO_CHANGE:
            # 幂等路径：不刷新 updated_at、不重算签名、不产生审计噪音。
            return state
        if not isinstance(new_state, dict):
            raise TypeError(
                f"mutator 必须返回 dict 或 NO_CHANGE，"
                f"实际返回 {type(new_state).__name__}")
        write_state(name, new_state)
        return new_state


# ── STATUS.json 全局任务汇总 ──

def _load_task_summary() -> Dict[str, Any]:
    """加载 STATUS.json 全局任务汇总"""
    if STATUS.exists():
        try:
            return json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"project": "", "updated_at": "", "tasks": {}}


def _save_task_summary(data: Dict[str, Any]):
    """保存 STATUS.json"""
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now_iso()
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def upsert_task_summary(name: str, **fields):
    """在 STATUS.json 中创建或更新一个 task 条目"""
    data = _load_task_summary()
    entry = data["tasks"].get(name, {})
    entry["id"] = name
    entry.update(fields)
    entry["updated_at"] = _now_iso()
    if "created_at" not in entry:
        entry["created_at"] = _now_iso()
    data["tasks"][name] = entry
    _save_task_summary(data)


def remove_task_summary(name: str):
    """从 STATUS.json 中移除一个 task 条目"""
    data = _load_task_summary()
    data["tasks"].pop(name, None)
    _save_task_summary(data)


def get_active_from_status() -> Optional[str]:
    """从 STATUS.json 的 tasks 中查找当前活跃任务。

    STATUS.json 是缓存，任务目录被外部删除（测试残留、手工 rm）后条目会留下，
    此时必须跳过，否则调用方拿到一个读不出 .state 的名字就直接报错退出。
    """
    data = _load_task_summary()
    tasks = data.get("tasks", {})
    candidates = []
    for name, entry in tasks.items():
        if entry.get("stage_status") not in ("running", "pending", "waiting"):
            continue
        if not state_path(name).exists():
            continue
        candidates.append((entry.get("updated_at", ""), name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def find_context_from_cwd(start_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """从当前目录向上查找 .sw-context 标记文件，返回项目上下文

    返回格式: {"project": "...", "target_dir": "...", "type": "...", "created": "..."}
    未找到时返回 None。
    """
    cwd = Path(start_dir).resolve() if start_dir else Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        marker = parent / ".sw-context"
        if marker.exists():
            try:
                return json.loads(marker.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None
