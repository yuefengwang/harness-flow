"""A3 的验收 1 / 2 / 3 / 19：事实包完整性与篡改检出。

2.9 已定性：`facts/` 是「agent 只读」的**约定**，不是强制 ——
A0 的 2.5 指出 Toolbox 对 opencode 无效，2.9.5 实测 `bash` 可绕过 deny。
所以 A3 不假装 `facts/` 不可写，而是让篡改**可检出**：
manifest 记各文件哈希，manifest 自身的哈希写入 `.state` 的 `facts` 键，
由 A0 的 HMAC 覆盖。
"""

import hashlib
import json

import pytest

from sw_lib.core import evidence as EV
from sw_lib.core import git_repo as G
from sw_lib.core.state import read_state, write_state
from sw_lib.workflow import fact_pack as FP


@pytest.fixture
def task_env(tmp_path, monkeypatch):
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    monkeypatch.setattr("sw_lib.core.config.TASKS", tasks_root, raising=False)
    monkeypatch.setattr("sw_lib.core.state.TASKS", tasks_root, raising=False)
    monkeypatch.setattr(FP, "TASKS", tasks_root, raising=False)

    task = "a3man"
    task_dir = tasks_root / task
    task_dir.mkdir()
    target = tmp_path / "repo" / task
    target.mkdir(parents=True)
    (target / "kept.py").write_text("x = 1\n", encoding="utf-8")
    info = G.ensure_repo(str(target), task)
    write_state(task, {
        "id": task, "stage": "03-coding", "stage_idx": 2,
        "stage_status": "running", "target_dir": str(target),
        "review": {"baseline_sha": info.sha, "baseline_kind": info.kind},
    })
    return task, task_dir, target


# ── 验收 1：九个文件齐备，verify() 返回空 ──

EXPECTED_FILES = {
    "diff.stat", "diff.patch", "diff.numstat", "diff.truncated",
    "tests.json", "coverage.json", "spec.md", "plan.md", "manifest.json",
}


def test_all_nine_files_present_and_verify_clean(task_env):
    task, task_dir, _ = task_env
    FP.generate(task)

    on_disk = {p.name for p in (task_dir / "facts").iterdir() if p.is_file()}
    assert EXPECTED_FILES <= on_disk, f"缺少：{sorted(EXPECTED_FILES - on_disk)}"
    assert FP.verify(task) == []


# ── 验收 2：manifest 中的哈希与磁盘内容一致 ──

def test_manifest_hashes_match_disk(task_env):
    task, task_dir, _ = task_env
    FP.generate(task)
    manifest = json.loads((task_dir / "facts" / "manifest.json")
                          .read_text(encoding="utf-8"))

    for name, digest in manifest["files"].items():
        body = (task_dir / "facts" / name).read_bytes()
        assert hashlib.sha256(body).hexdigest() == digest, f"{name} 哈希不符"


def test_manifest_does_not_hash_itself(task_env):
    """manifest 不能把自己列进 files —— 那是个无解的自指。"""
    task, task_dir, _ = task_env
    FP.generate(task)
    manifest = json.loads((task_dir / "facts" / "manifest.json")
                          .read_text(encoding="utf-8"))

    assert "manifest.json" not in manifest["files"]


# ── 验收 3：每次进入 04 都重新生成 ──

def test_regeneration_reflects_new_changes(task_env):
    task, task_dir, target = task_env
    FP.generate(task)
    first = (task_dir / "facts" / "diff.stat").read_text(encoding="utf-8")

    (target / "added_later.py").write_text("b = 2\n", encoding="utf-8")
    FP.generate(task)
    second = (task_dir / "facts" / "diff.stat").read_text(encoding="utf-8")

    assert first != second
    assert "added_later.py" in second


# ── 验收 19：篡改检出（两半都要验）──

def test_tampered_fact_file_is_detected_by_verify(task_env):
    task, task_dir, _ = task_env
    FP.generate(task)

    (task_dir / "facts" / "diff.stat").write_text("伪造的统计\n", encoding="utf-8")

    missing = FP.verify(task)
    assert any("diff.stat" in m for m in missing), \
        f"篡改未被检出，verify 返回 {missing!r}"


def test_tampering_manifest_too_is_caught_by_evidence_signature(task_env):
    """第二半同时验证**落点正确**（A3 的 3.6）。

    manifest 哈希若没写在 `.state` 的 `facts` 键下，HMAC 签名不覆盖它，
    连 manifest 一起改就无人发现 —— 这条断言会红。
    """
    task, task_dir, _ = task_env
    FP.generate(task)

    assert EV.verify_evidence(read_state(task)).status == "valid"

    facts_file = task_dir / "facts" / "diff.stat"
    facts_file.write_text("伪造的统计\n", encoding="utf-8")
    manifest_path = task_dir / "facts" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["diff.stat"] = hashlib.sha256(
        facts_file.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                             encoding="utf-8")

    # manifest 自洽了，verify() 的哈希比对过关 ——
    assert FP.verify(task) == []
    # —— 但 .state 里记着 manifest 的原始哈希，且它在 HMAC 覆盖范围内。
    assert FP.manifest_matches_state(task) is False


def test_manifest_hash_lands_under_signed_facts_key(task_env):
    task, task_dir, _ = task_env
    FP.generate(task)
    state = read_state(task)

    assert "facts" in EV.EVIDENCE_FIELDS
    assert state.get("facts", {}).get("manifest_sha256")
    # 直接改签名覆盖范围内的字段 → tampered
    state["facts"]["manifest_sha256"] = "0" * 64
    assert EV.verify_evidence(state).status == "tampered"


# ── 3.8：截断与二进制 ──

def test_binary_file_appears_in_file_list(task_env):
    """R6 / 2.8：二进制无行数，不参与排序，但必须出现在清单里 —— 否则等于隐身。"""
    task, task_dir, target = task_env
    (target / "blob.bin").write_bytes(bytes(range(256)) * 8)

    pack = FP.generate(task)
    numstat = (task_dir / "facts" / "diff.numstat").read_text(encoding="utf-8")

    assert "blob.bin" in numstat


def test_truncation_is_recorded_and_warned(task_env, monkeypatch):
    task, task_dir, target = task_env
    monkeypatch.setattr(FP, "PATCH_BYTE_LIMIT", 200)
    (target / "big.py").write_text("# pad\n" * 4000, encoding="utf-8")

    pack = FP.generate(task)

    truncated = (task_dir / "facts" / "diff.truncated").read_text(encoding="utf-8")
    assert "big.py" in truncated
    assert pack.warnings, "触发截断却没有 warnings —— 截断必须可见，不得静默"
    assert any("截断" in w for w in pack.warnings)


def test_mock_mode_still_generates_pack(task_env, monkeypatch):
    """A3 的第 4 节：mock 影响的是「测试是否真跑」，不是「事实包是否生成」。

    若 mock 下允许跳过基线校验，A3 的链路就从未被测过。
    """
    task, task_dir, _ = task_env
    monkeypatch.setattr(FP, "is_mock_agent", lambda: True)

    pack = FP.generate(task)
    manifest = json.loads((task_dir / "facts" / "manifest.json")
                          .read_text(encoding="utf-8"))

    assert manifest["mock"] is True
    assert (task_dir / "facts" / "diff.numstat").exists()
    tests = json.loads((task_dir / "facts" / "tests.json").read_text(encoding="utf-8"))
    assert tests.get("mock") is True
