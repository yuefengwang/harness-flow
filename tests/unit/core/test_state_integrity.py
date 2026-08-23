"""A0 第一层：`.state` 完整性。

对应 docs/design/A0-state-integrity.md 的 D0-1（移除自动迁移回写）与
D0-2（原子写），验收标准第 1-3 条。

纪律（A0 的 9.3）：这些用例全部操作**真实文件系统** —— 截断语义、
`os.replace` 的原子性都不能 mock，mock 掉之后验证的是自己对 POSIX 的理解，
而不是 POSIX 的实际行为。
"""

import json
import threading
import time

import pytest

from sw_lib.core import state as state_mod


@pytest.fixture
def iso_task(tmp_path, monkeypatch):
    """把 TASKS 指向 tmp_path，拿到一个隔离的任务名。

    不用 conftest 的 dummy_task —— 那个写在真实 workspace 里，
    而本文件要故意制造损坏的 .state，不能污染用户的任务目录。
    """
    monkeypatch.setattr(state_mod, "TASKS", tmp_path)
    name = "t-integrity"
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return name


def _truncated_json(state: dict) -> str:
    """构造一份「写到一半」的 JSON —— 非原子写崩溃后磁盘上的真实形态。"""
    text = json.dumps(state, ensure_ascii=False, indent=2)
    return text[: len(text) // 2]


# ── D0-1：读到损坏内容时不得回写、不得伪装 ──

def test_corrupted_state_is_not_rewritten_on_read(iso_task):
    """验收 1：截断的 .state 被读取后，磁盘内容必须原样保留。

    现状（A0 的 2.1 已实测）：read_state 把截断内容当成旧的 `key: value`
    格式解析成功，并在 state.py:64 回写，把损坏**固化**成一份看起来
    合法的垃圾（键名带引号、red_witness 整棵子树丢失）。
    """
    sf = state_mod.state_path(iso_task)
    broken = _truncated_json({
        "id": iso_task,
        "stage": "03-coding",
        "stage_idx": 2,
        "red_witness": {"failed_nodes": ["t.py::a"]},
    })
    sf.write_text(broken, encoding="utf-8")

    state_mod.read_state(iso_task)

    assert sf.read_text(encoding="utf-8") == broken, (
        "read_state 回写了损坏的 .state —— 损坏被固化，原始内容已不可恢复"
    )


def test_corrupted_state_is_flagged_not_silently_defaulted(iso_task):
    """验收 2：损坏的 .state 不得被伪装成「处于第一阶段的新任务」。

    这是现状里最恶劣的静默降级：state.py:69/:75 的默认值填充，
    把「读不出来」变成了 stage=01-brainstorming + stage_idx=0。
    调用方无从分辨这是新任务还是损坏。
    """
    sf = state_mod.state_path(iso_task)
    sf.write_text(
        _truncated_json({"id": iso_task, "stage": "04-review", "stage_idx": 3}),
        encoding="utf-8",
    )

    st = state_mod.read_state(iso_task)

    assert st.get("_corrupted") is True, (
        f"损坏未被标记，调用方无法分辨；实际返回 stage={st.get('stage')!r}"
    )
    assert st.get("stage") != "01-brainstorming", (
        "损坏的 .state 被静默填充成第一阶段 —— 阶段发生了无声回退"
    )


def test_valid_state_still_round_trips(iso_task):
    """单调性：合法 JSON 的读写行为不受本次改动影响。"""
    payload = {
        "id": iso_task,
        "stage": "03-coding",
        "stage_idx": 2,
        "red_witness": {"failed_nodes": ["t.py::a"]},
    }
    state_mod.write_state(iso_task, payload)

    st = state_mod.read_state(iso_task)

    assert st["stage"] == "03-coding"
    assert st["stage_idx"] == 2
    assert st["red_witness"] == {"failed_nodes": ["t.py::a"]}
    assert not st.get("_corrupted")


def test_empty_state_file_is_not_corrupted(iso_task):
    """空文件是合法的「尚无状态」，不是损坏 —— 不得误报。"""
    state_mod.state_path(iso_task).write_text("", encoding="utf-8")

    st = state_mod.read_state(iso_task)

    assert not st.get("_corrupted")


# ── D0-2：原子写 ──

def test_write_state_is_atomic_on_crash(iso_task):
    """验收 3：写入过程中崩溃时，原 .state 必须保持完整可读。

    现状（2.2）：write_state 用 open(sf, "w") —— 先截断再写，
    因此崩溃窗口内文件必然是半截的，正好落进 D0-1 的固化路径。

    崩溃用一个不可 JSON 序列化的值触发（真实场景：某调用方塞进了
    Path / datetime 对象）。已实测：`json.dump` 写到该字段时抛 TypeError，
    而此时 `open(sf, "w")` 早已把原文件截断 —— 磁盘上留下半截内容。

    刻意不 monkeypatch `os.replace`：那要求测试知道实现用了哪个系统调用，
    实现方式一变测试就失效，且失败会是 AttributeError 而非断言失败
    （DEV-PROTOCOL 1.1：那不算有效的红）。这里只断言外部可观察的性质 ——
    **写失败后旧内容仍在**。
    """
    good = {"id": iso_task, "stage": "03-coding", "stage_idx": 2}
    state_mod.write_state(iso_task, good)
    before = state_mod.state_path(iso_task).read_text(encoding="utf-8")

    with pytest.raises(TypeError):
        state_mod.write_state(iso_task, {
            "id": iso_task,
            "stage": "05-archive",
            "stage_idx": 4,
            "doomed": object(),   # 不可序列化 → json.dump 中途抛错
        })

    after = state_mod.state_path(iso_task).read_text(encoding="utf-8")
    assert after == before, "崩溃后 .state 被破坏 —— 写入不是原子的"
    assert json.loads(after)["stage"] == "03-coding"


def test_write_state_leaves_no_temp_files(iso_task):
    """原子写会建临时文件，成功路径上必须清理干净。"""
    state_mod.write_state(iso_task, {"id": iso_task, "stage": "03-coding"})

    task_dir = state_mod.state_path(iso_task).parent
    leftovers = [p.name for p in task_dir.iterdir() if p.name != ".state"]
    assert leftovers == [], f"残留临时文件: {leftovers}"


# ── D0-3：update_state 串行化 read-modify-write ──

def test_concurrent_updates_do_not_lose_writes(iso_task):
    """验收 4：两个并发 update_state 的修改都必须保留。

    现状（A0 的 2.3 已实测）：各调用方 read_state → 改 → write_state，
    后写者静默覆盖前写者。04 阶段有客观轨、攻击者、仲裁器三处写入，
    Web 与 TUI 可同时运行，HealthMonitor 还在独立线程里写。

    刻意用**真实线程**而非顺序调用 —— 顺序调用永远不会丢失，
    那样测不出竞争（A0 的 9.2「假绿风险」列的正是这条）。
    两个线程各自在 mutator 里 sleep，把交错窗口拉到必然发生。
    """
    state_mod.write_state(iso_task, {"id": iso_task, "stage": "04-review"})

    def make_mutator(key, value):
        def _m(st):
            # 读到之后停一下：无锁实现里，这段时间足够另一方读到同一份旧状态
            time.sleep(0.05)
            st[key] = value
            return st
        return _m

    errors = []

    def worker(key, value):
        try:
            state_mod.update_state(iso_task, make_mutator(key, value))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [
        threading.Thread(target=worker, args=("objective", {"O3": "fail"})),
        threading.Thread(target=worker, args=("adversary", {"cx": "t.py::x"})),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"update_state 抛异常: {errors}"

    final = state_mod.read_state(iso_task)
    assert final.get("objective") == {"O3": "fail"}, "objective 的写入丢失了"
    assert final.get("adversary") == {"cx": "t.py::x"}, "adversary 的写入丢失了"


def test_lock_file_is_separate_from_state_file(iso_task):
    """验收 5：锁不能建在 `.state` 自身上。

    原子写会用 os.replace 换掉 `.state` 的 inode。若锁持在 `.state` 上，
    替换之后持锁方守着的是一个已被解链的旧 inode —— 互斥静默失效，
    而且**表面上一切正常**，这是最难发现的一类失效。

    判定方式：在 update_state 执行期间（此时锁应被持有），
    观察任务目录里出现的锁文件不是 `.state` 本身。
    """
    state_mod.write_state(iso_task, {"id": iso_task, "stage": "04-review"})
    task_dir = state_mod.state_path(iso_task).parent
    seen = []

    def _m(st):
        seen.extend(sorted(p.name for p in task_dir.iterdir()))
        return st

    state_mod.update_state(iso_task, _m)

    lock_names = [n for n in seen if "lock" in n.lower()]
    assert lock_names, (
        f"未见任何锁文件，锁可能持在 .state 自身上；目录内容: {seen}"
    )


def test_update_state_refuses_to_run_on_corrupted_state(iso_task):
    """损坏的 .state 上不得执行 read-modify-write。

    否则 mutator 会基于 `{"_corrupted": True}` 这份空壳做修改并写回，
    把损坏彻底覆盖 —— 原始内容就再也拿不回来了。
    """
    state_mod.state_path(iso_task).write_text(
        _truncated_json({"id": iso_task, "stage": "03-coding", "stage_idx": 2}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        state_mod.update_state(iso_task, lambda st: {**st, "touched": True})


def test_update_state_returns_persisted_state(iso_task):
    """update_state 应返回落盘后的状态，省掉调用方再读一次。"""
    state_mod.write_state(iso_task, {"id": iso_task, "stage": "04-review"})

    result = state_mod.update_state(
        iso_task, lambda st: {**st, "route": "05-Archive"})

    assert result["route"] == "05-Archive"
    assert state_mod.read_state(iso_task)["route"] == "05-Archive"


# ── D0-1 的兜底：损坏标记不得被任何写路径回写 ──

def test_write_state_refuses_to_persist_corruption_marker(iso_task):
    """`_corrupted` 空壳绝不能被写回磁盘。

    这是 D0-1 的一个漏洞（实现 update_state 后实测发现）：
    read_state 现在对损坏返回 `{"_corrupted": True, ...}`，
    但全仓库有约 12 处 `read_state` → 改 → `write_state` 的调用点。
    它们拿到这份空壳后照常修改并写回，**结果是把损坏内容覆盖掉** ——
    正好摧毁 D0-1 想保住的东西。

    实测复现：stage_state.seed_gate() 在损坏的 .state 上返回 True，
    并把文件改成 `{"_corrupted": true, ..., "stages": {...}}`。

    逐个改 12 处调用点既繁琐又必然漏。改在 write_state 这个共同瓶颈上：
    任何试图持久化 `_corrupted` 标记的写入都拒绝。
    """
    sf = state_mod.state_path(iso_task)
    broken = _truncated_json({"id": iso_task, "stage": "04-review",
                              "stage_idx": 3})
    sf.write_text(broken, encoding="utf-8")

    st = state_mod.read_state(iso_task)
    assert st.get("_corrupted") is True   # 前提

    # 模拟任意一个调用方：拿到空壳、加了自己的字段、写回
    st["stages"] = {"04-review": {"gate": {}}}
    with pytest.raises(ValueError):
        state_mod.write_state(iso_task, st)

    assert sf.read_text(encoding="utf-8") == broken, (
        "损坏内容被覆盖 —— D0-1 的保护被下游写点绕过了"
    )


def test_seed_gate_does_not_clobber_corrupted_state(iso_task):
    """真实调用方视角：stage_state.seed_gate 不得摧毁损坏的 .state。

    直接验证实测中发现问题的那个函数，而不只验证 write_state 的内部约束 ——
    否则「瓶颈处已拦住」只是我的假设。
    """
    from sw_lib.workflow import stage_state as ss_mod

    sf = state_mod.state_path(iso_task)
    broken = _truncated_json({"id": iso_task, "stage": "04-review",
                              "stage_idx": 3})
    sf.write_text(broken, encoding="utf-8")

    with pytest.raises(ValueError):
        ss_mod.seed_gate(iso_task, "04-review")

    assert sf.read_text(encoding="utf-8") == broken


# ── D0-1 的收尾：`_corrupted` 必须有调用方真正处理 ──
#
# 上一轮交付时这是遗留项：`rg _corrupted` 只在 state.py 自身命中，
# 35 处 read_state 调用点无人检查。实测后果是「数据保住了，但表现很差」：
#   - write_state 拒绝持久化（数据安全 ✅）
#   - 但错误是深处抛出的裸 ValueError，用户看到的是崩栈而非可行动的提示
#   - get_task_state 直接把 _corrupted 空壳交给调用方，无任何信号

def test_corrupted_state_is_detectable_by_helper(iso_task):
    """提供显式判定函数，调用方不必去猜 `_corrupted` 这个私有键名。"""
    state_mod.state_path(iso_task).write_text(
        '{ "id": "x", "stage": "03-cod', encoding="utf-8")

    st = state_mod.read_state(iso_task)

    assert state_mod.is_corrupted(st) is True
    assert state_mod.is_corrupted({"stage": "03-coding"}) is False
    assert state_mod.is_corrupted({}) is False


def test_advance_fails_fast_with_actionable_message(iso_task):
    """推进阶段遇到损坏状态，必须给出可行动的错误，而不是深处的裸 ValueError。

    实测改前：`WorkflowRuntime.advance` 一路走到 `write_state` 才抛
    `ValueError: 拒绝写入带 _corrupted 标记的状态`，栈很深，且提示是
    面向实现者的（"调用方读到损坏的 .state 后仍继续修改并回写"），
    对用户毫无指引 —— 他需要知道的是「哪个文件坏了、怎么修」。
    """
    state_mod.state_path(iso_task).write_text(
        '{ "id": "x", "stage": "05-arch', encoding="utf-8")

    with pytest.raises(state_mod.StateCorruptedError) as ei:
        state_mod.raise_if_corrupted(state_mod.read_state(iso_task), iso_task)

    msg = str(ei.value)
    assert iso_task in msg, "错误信息未指明是哪个任务"
    assert ".state" in msg, "错误信息未指明是哪个文件"
    assert "migrate_state_format" in msg, "未给出修复路径（迁移脚本）"


def test_corrupted_error_is_valueerror_subclass():
    """既有调用点可能 `except ValueError`，新异常必须兼容，不改变捕获行为。"""
    assert issubclass(state_mod.StateCorruptedError, ValueError)


def test_advance_rejects_corrupted_state_at_entry(iso_task, monkeypatch):
    """**从真实调用方视角**验证：`advance` 必须在入口拒绝，而不是走到写盘才炸。

    这条与 2.8 的教训同源 —— 只验证 `raise_if_corrupted` 自己的行为，
    等于假设「有人会调用它」。而上一轮的遗留问题恰恰就是**没人调用**。
    """
    from sw_lib.workflow import runtime as rt

    monkeypatch.setattr(rt, "TASKS", state_mod.TASKS, raising=False)
    state_mod.state_path(iso_task).write_text(
        '{ "id": "x", "stage_idx": 4, "stage": "05-arch', encoding="utf-8")

    with pytest.raises(state_mod.StateCorruptedError) as ei:
        rt.WorkflowRuntime.advance(iso_task)

    assert "修复" in str(ei.value), "错误未包含修复指引"
    # 原文必须完好 —— 这是 D0-1 的全部意义
    assert state_mod.state_path(iso_task).read_text(encoding="utf-8") == \
        '{ "id": "x", "stage_idx": 4, "stage": "05-arch'


# ── R1 收尾：把真正的 read-modify-write 序列迁到 update_state ──
#
# 上一轮遗留：`update_state` 建好了但无人使用（39 处写入点未迁移）。
# 全量迁移面太大，但**判据类写入**不能等 —— A5 的并行 reviewer 会同时
# 写 route/decision，丢写等于丢判据。先迁 stage_state.py 的 7 处。

def test_stage_state_writers_go_through_controlled_entry():
    """`stage_state.py` 的判据写入必须走 `update_state`，不得裸用 `write_state`。

    为什么用源码断言而不是并发断言：我先写了一个「并发签 Gate + 写 Route」的
    用例，**它在迁移前就是绿的** —— 因为两者落在不同子键，且各自幂等，
    交错也看不出差别。那种测试给人虚假的安全感。

    真正的丢写发生在多个写入者修改**同一子树**时。已实测（15 次 x 3 线程）：

        裸 read_state → 改 → write_state ：期望 45 条，实际 15 条，丢 30 条
        update_state                      ：期望 45 条，实际 45 条

    A5 的并行 reviewer 各写自己的结论进同一棵 review 子树，正是这个形状。
    因此这里守的是「入口纪律」，配合下面那条真实丢写用例。
    """
    import inspect
    from sw_lib.workflow import stage_state as ss

    src = inspect.getsource(ss)
    # 允许 import 行出现 write_state，但函数体内不得调用
    calls = [ln.strip() for ln in src.splitlines()
             if "write_state(" in ln and not ln.strip().startswith(("from", "import", "#"))]
    assert not calls, (
        "stage_state.py 仍有裸 write_state 调用，并发下会整体覆盖他人修改:\n  "
        + "\n  ".join(calls))


def test_naive_read_modify_write_loses_data_but_update_state_does_not(iso_task):
    """量化对照：证明 `update_state` 解决的是一个真实存在的丢写问题。

    这条同时是 `update_state` 的价值证明。若哪天有人觉得锁开销大想去掉，
    这里的数字会立刻告诉他代价是什么。
    """
    def naive(key):
        for i in range(12):
            st = state_mod.read_state(iso_task)
            time.sleep(0.002)                       # 放大 read→write 窗口
            st.setdefault("reviews", {})[f"{key}-{i}"] = True
            state_mod.write_state(iso_task, st)

    def safe(key):
        for i in range(12):
            def mut(st, k=key, n=i):
                st.setdefault("reviews", {})[f"{k}-{n}"] = True
                return st
            state_mod.update_state(iso_task, mut)

    def run(target):
        state_mod.update_state(iso_task, lambda st: {**st, "id": iso_task,
                                                    "reviews": {}})
        ts = [threading.Thread(target=target, args=(k,)) for k in "abc"]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        return len(state_mod.read_state(iso_task).get("reviews", {}))

    lost = run(naive)
    kept = run(safe)

    assert kept == 36, f"update_state 也丢写了：{kept}/36"
    assert lost < 36, (
        f"裸 read/write 竟然没丢写（{lost}/36）—— 说明本用例的交错窗口不够，"
        f"它就无法证明 update_state 的价值")


def test_update_state_supports_abort_without_writing(iso_task):
    """mutator 可以决定「无事可做」，此时不得写盘。

    为什么需要这个原语：`seed_gate` 是幂等的（已播种就返回 False），
    `issue_output_nonce` 在 nonce 已存在时也不写。若强迫它们必须写一次，
    每次读取都会刷新 `updated_at` 并重算签名 —— 把只读操作伪装成写操作，
    审计里会出现大量无意义的状态变更。
    """
    state_mod.update_state(iso_task, lambda st: {**st, "id": iso_task,
                                                 "keep": "me"})
    before = state_mod.state_path(iso_task).stat().st_mtime_ns

    result = state_mod.update_state(iso_task, lambda st: state_mod.NO_CHANGE)

    after = state_mod.state_path(iso_task).stat().st_mtime_ns
    assert after == before, "返回 NO_CHANGE 仍然写盘了"
    assert result["keep"] == "me", "NO_CHANGE 应返回当前已落盘状态"


def test_status_flip_does_not_erase_gate_signature(iso_task, monkeypatch):
    """**实测出来的真实缺陷**：翻 `stage_status` 会抹掉用户刚签的 Gate。

    场景完全来自生产代码路径：

      - `base.py:162` 在 agent 跑完时把 `stage_status` 翻成 `idle`
      - 用户同时在 TUI 按 `[A]` 签 Gate（`sign_gate`）

    前者读到签署之前的旧快照，改完整体写回，**签署凭空消失**。
    实测（15 次交错）：`gate.signed_by` 为 None，用户的签署被吞掉。

    `runtime.py:114` 那句注释（"reset_gate / reset_route 刚刚写过盘，
    直接拿旧快照写回去会把重置结果整体覆盖掉"）说明这个坑早被踩过，
    但当时是就地重读绕过去的 —— 只修了那一处，同一形状的其他写入点还在。
    """
    from sw_lib.workflow import stage_state as ss
    from sw_lib.workflow import runtime as rt

    monkeypatch.setattr(ss, "TASKS", state_mod.TASKS, raising=False)
    state_mod.update_state(iso_task, lambda st: {
        **st, "id": iso_task, "stage": "04-review", "stage_idx": 3})

    errors = []

    def flip():
        try:
            for _ in range(15):
                rt.WorkflowRuntime.set_stage_status(iso_task, "idle")
                time.sleep(0.002)
        except BaseException as e:                  # noqa: BLE001
            errors.append(e)

    def sign():
        try:
            for _ in range(15):
                ss.sign_gate(iso_task, "04-review")
                time.sleep(0.003)
        except BaseException as e:                  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=flip), threading.Thread(target=sign)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # 线程里的异常不会让测试变红 —— 不显式收集，缺函数也会"通过"。
    # 我第一版就栽在这：set_stage_status 不存在，flip 线程直接死掉，用例反而绿了。
    assert not errors, f"并发线程抛异常: {errors!r}"

    gate = (state_mod.read_state(iso_task).get("stages", {})
            .get("04-review", {}).get("gate", {}))
    assert gate.get("signed_by") == "user", (
        "并发翻转 stage_status 抹掉了用户的 Gate 签署 —— 判据丢失")
