"""A2 的 3.4：测试文件哈希冻结，以及 03b 的转绿判定。

哈希校验是**长期主防线**，不是 A0 未就绪时的临时替代 ——
依据 A0 的 U0-1：`bash` 工具无法按路径约束，写入拦截拦不住有 shell 的
agent。因此防线只能落在准出时的比对上（A2 的 3.3 末尾）。
"""

import pytest

from sw_lib.workflow import red_witness as rw


def _proj(tmp_path, files):
    root = tmp_path / "p"
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


# ── 哈希扫描的范围 ──

def test_hashes_cover_test_files_only(tmp_path):
    """只冻结测试文件；实现文件必须能自由改动，否则 03b 无法工作。

    识别依据是路径（A2 的 3.3）：`test_*.py` / `*_test.py` / `tests/` 下。
    """
    proj = _proj(tmp_path, {
        "test_a.py": "def test_a():\n    assert 0\n",
        "b_test.py": "def test_b():\n    assert 0\n",
        "tests/test_c.py": "def test_c():\n    assert 0\n",
        "impl.py": "def f():\n    return 1\n",
        "tests/conftest.py": "import pytest\n",
    })

    frozen = rw.hash_test_files(proj)

    assert set(frozen) == {"test_a.py", "b_test.py", "tests/test_c.py",
                           "tests/conftest.py"}, \
        f"冻结范围不对: {sorted(frozen)}"
    assert all(len(v) == 64 for v in frozen.values()), \
        f"哈希不是 sha256 十六进制: {frozen}"


def test_hashes_ignore_caches(tmp_path):
    """`__pycache__` / `.venv` 里的东西不是 agent 的产出，冻结它们会自伤。"""
    proj = _proj(tmp_path, {
        "test_a.py": "def test_a():\n    assert 0\n",
        "__pycache__/test_a.cpython-312.pyc": "binary-ish",
        ".venv/lib/test_dep.py": "def test_dep():\n    pass\n",
    })

    frozen = rw.hash_test_files(proj)
    assert set(frozen) == {"test_a.py"}, f"缓存被纳入冻结: {sorted(frozen)}"


# ── 比对：改了测试必须被拒，并指名文件 ──

def test_unchanged_hashes_pass(tmp_path):
    proj = _proj(tmp_path, {"test_a.py": "def test_a():\n    assert 0\n"})
    frozen = rw.hash_test_files(proj)

    verdict = rw.compare_hashes(frozen, rw.hash_test_files(proj))
    assert verdict.ok, f"测试文件没动却被拒: {verdict.reason}"


def test_modified_test_file_is_rejected_with_filename(tmp_path):
    """验收 6：03b 改了测试文件必须被拒绝，且提示**具体文件**。

    「改测试让它过」是最常见的自欺路径（A2 的 F4 / DEV-PROTOCOL 1.2）。
    只说「哈希不一致」等于让用户去猜是哪个文件。
    """
    proj = _proj(tmp_path, {
        "test_a.py": "def test_a():\n    assert f() == 2\n",
        "test_b.py": "def test_b():\n    assert 0\n",
    })
    frozen = rw.hash_test_files(proj)

    # 模拟 agent 把断言改松
    (proj / "test_a.py").write_text("def test_a():\n    assert True\n",
                                    encoding="utf-8")

    verdict = rw.compare_hashes(frozen, rw.hash_test_files(proj))
    assert not verdict.ok, "测试文件被改写却通过了哈希校验"
    assert "test_a.py" in verdict.reason, \
        f"未指出被改动的文件: {verdict.reason}"
    assert "test_b.py" not in verdict.reason, \
        f"未被改动的文件不该出现在拒绝理由里: {verdict.reason}"


def test_deleted_test_file_is_rejected(tmp_path):
    """删掉测试文件同样是绕过 —— 不能只查「内容变了」。"""
    proj = _proj(tmp_path, {"test_a.py": "def test_a():\n    assert 0\n"})
    frozen = rw.hash_test_files(proj)
    (proj / "test_a.py").unlink()

    verdict = rw.compare_hashes(frozen, rw.hash_test_files(proj))
    assert not verdict.ok, "测试文件被删除却通过了校验"
    assert "test_a.py" in verdict.reason, verdict.reason


def test_added_test_file_is_allowed(tmp_path):
    """新增测试不算篡改：补测试是好事，冻结要防的是**改已有判据**。"""
    proj = _proj(tmp_path, {"test_a.py": "def test_a():\n    assert 0\n"})
    frozen = rw.hash_test_files(proj)
    (proj / "test_new.py").write_text("def test_n():\n    assert True\n",
                                      encoding="utf-8")

    verdict = rw.compare_hashes(frozen, rw.hash_test_files(proj))
    assert verdict.ok, f"新增测试文件被误判为篡改: {verdict.reason}"


def test_bypass_write_is_still_detected(tmp_path):
    """验收 10（有效性类）：用 `python -c` 改测试文件，仍能被检出。

    A0 的验收 14 已证明 argv 前缀校验拦不住解释器写文件，
    所以这里验证的是**准出时的比对**这条防线仍然成立。
    """
    import subprocess
    import sys

    proj = _proj(tmp_path, {"test_a.py": "def test_a():\n    assert 1 == 2\n"})
    frozen = rw.hash_test_files(proj)

    target = proj / "test_a.py"
    subprocess.run(
        [sys.executable, "-c",
         f"open({str(target)!r}, 'w').write('def test_a():\\n    assert True\\n')"],
        check=True, capture_output=True)

    verdict = rw.compare_hashes(frozen, rw.hash_test_files(proj))
    assert not verdict.ok, "绕过写入未被哈希校验检出 —— 主防线失守"
    assert "test_a.py" in verdict.reason, verdict.reason


# ── 03b 转绿判定 ──

def test_green_requires_all_frozen_nodes_passed(tmp_path):
    """验收 7：已见证的失败节点必须全部转绿。"""
    verdict = rw.verify_green(
        ["test_y.py::test_a", "test_y.py::test_b"],
        {"test_y.py::test_a": "passed", "test_y.py::test_b": "passed"},
        0)
    assert verdict.ok, f"全部转绿却被拒: {verdict.reason}"
    assert set(verdict.passed_nodes) == {"test_y.py::test_a",
                                        "test_y.py::test_b"}


def test_skipped_frozen_node_is_not_green(tmp_path):
    """A2 的 10.4 指定的红：把已见证的失败测试标上 skip，必须被拒。

    这条测不出来就说明实现只看了退出码 —— 全 skip 的退出码是 0。
    """
    verdict = rw.verify_green(
        ["test_y.py::test_a"],
        {"test_y.py::test_a": "skipped"},
        0)
    assert not verdict.ok, "已见证节点被 skip 掉却算作转绿"
    assert "test_y.py::test_a" in verdict.reason, verdict.reason
    assert "skip" in verdict.reason.lower(), verdict.reason


def test_vanished_frozen_node_is_not_green():
    """节点整个消失（测试被删/改名）也不算绿，且要指名。"""
    verdict = rw.verify_green(["test_y.py::test_a"],
                              {"test_y.py::test_other": "passed"}, 0)
    assert not verdict.ok, "已见证节点消失却算作转绿"
    assert "test_y.py::test_a" in verdict.reason, verdict.reason


def test_nonzero_exit_is_not_green():
    """套件里别的测试红着，也不算转绿（单调性，A2 的 4.1）。"""
    verdict = rw.verify_green(
        ["test_y.py::test_a"],
        {"test_y.py::test_a": "passed", "test_y.py::test_new": "failed"},
        1)
    assert not verdict.ok, "退出码非 0 却算作转绿"
    assert "test_y.py::test_new" in verdict.reason, verdict.reason


def test_timeout_is_unavailable_not_green():
    """A2 的 R3：超时记为 unavailable，不算通过。"""
    verdict = rw.verify_green(["test_y.py::test_a"], {}, -1)
    assert not verdict.ok, "执行未完成却算作转绿"
    assert verdict.status == "unavailable", verdict.status
