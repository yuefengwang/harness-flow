#!/usr/bin/env python3
"""列出形状库里登记的判据中，没有生产消费方的那些。

存在理由：`harness-criterion-design` 技能的最后一步要求跑这个脚本，
而它此前并不存在 —— 「判据存在、无人调用」（形状 S7）犯在了技能自己身上。

用法::

    python3 scripts/orphan_criteria.py

退出码 0 表示登记的判据都有生产调用点，1 表示存在孤儿判据。
判据清单见 docs/design/CLOSED-LOOP.md 的形状库。
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 只算生产路径。测试与文档里的出现不构成「有人调用」——
# 这正是 S7 的判别点：定义 + 测试齐全，但没有任何生产代码用它。
PRODUCTION_ROOTS = ("sw_lib", "hooks", "bin", "sw", "templates", "config")

EXCLUDED_PREFIXES = ("tests/", "docs/", "workspace/", "repo/", "scripts/")

# (判据名, 判例出处, 形状编号)
REGISTERED_CRITERIA = [
    ("read_ambiguity_score", "2.9.10", "S3"),
    ("check_tamper", "2.9.10", "S9"),
    ("_stage_file_write_rules", "2.9.10", "S2"),
    ("is_idle", "2.9.9", "S7"),
    ("idle_seconds", "2.9.7", "S6"),
    ("note_activity", "2.9.7", "S6"),
    ("describe_tools", "2.9.7", "S2"),
    ("output_region", "2.9.7", "S5"),
    ("_build_project_info", "2.9.7", "S4"),
    ("set_stage_status", "2.9.6", "S6"),
    ("extract_claims_from_stage_file", "2.9.11", "S1"),
    ("_record_coding_claims", "2.9.11", "S1"),
    ("_readme_check", "2.9.11", "S2"),
    ("_substance_report", "2.9.12", "S1"),
    ("resolve_pytest_root", "2.9.14/2.9.15", "S4"),
    ("_project_python", "2.9.14/2.9.15", "S4"),
    ("_has_pytest_surface", "2.9.15", "S4"),
    ("effective_chat_timeout", "2.9.13", "S8"),
    ("waiting_for_human", "2.9.13", "S8"),
    ("declared_dependencies", "2.9.16", "S4"),
    ("missing_dependencies", "2.9.16", "S4"),
    ("install_hint", "2.9.16", "S4"),
    ("_diagnose_missing_deps", "2.9.16", "S10"),
    ("read_ambiguity_record", "2.9.17", "S7"),
    ("record_ambiguity", "2.9.17", "S6"),
    ("_read_ambiguity_carryover", "2.9.17", "S7"),
    ("_stage_completion_problems", "2.9.18", "S11"),
    ("empty_test_dirs", "2.9.19", "S10"),
    ("_no_tests_lines", "2.9.19", "S10"),
    # A14：判据本身曾是 S7 —— validate_config 里 6 条 error 级校验
    # 从未在生产路径上执行过，因为 assert_config_valid 只被测试调用。
    ("assert_config_valid", "2.9.20", "S7"),
    ("validate_config", "2.9.20", "S7"),
    # A15：`.state` 的受控写入入口。登记它们的理由与上面一样 ——
    # `update_state` 建成之后，仍有 11 处调用点走裸 write_state（形状 S10），
    # 判据在、没接上。`finish` 是三个终态写入点收拢后的唯一出口。
    ("update_state", "2.9.21", "S10"),
    ("finish", "2.9.21", "S10"),
    ("_write_state_safe", "2.9.21", "S6"),
]


def _rg_available() -> bool:
    try:
        subprocess.run(["rg", "--version"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def find_production_callers(name: str) -> list[str]:
    """返回生产路径中提到该名字的 `路径:行号` 列表，已排除定义行。

    「定义行」按语言形态判断：Python 的 `def name(` / `class name(`，
    以及 shell 的 `name()`。赋值不算定义 —— 常量被赋值之后仍然需要有人读它。
    """
    result = subprocess.run(
        ["rg", "--no-heading", "-n", "--", name, *PRODUCTION_ROOTS],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO,
    )
    callers: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        rel, lineno, content = parts[0], parts[1], parts[2]
        if rel.startswith(EXCLUDED_PREFIXES):
            continue
        stripped = content.strip()
        is_definition = (
            stripped.startswith(f"def {name}")
            or stripped.startswith(f"async def {name}")
            or stripped.startswith(f"class {name}")
            or stripped.startswith(f"{name}()")
        )
        if is_definition:
            continue
        callers.append(f"{rel}:{lineno}")
    return callers


def main() -> int:
    if not _rg_available():
        print("❓ rg 不可用，无法扫描 —— 这不算通过（DEV-PROTOCOL 第 2 节）。")
        return 2

    print(f"检查 {len(REGISTERED_CRITERIA)} 个已登记判据的生产调用点\n")
    orphans: list[tuple[str, str, str]] = []

    for name, source, shape in REGISTERED_CRITERIA:
        callers = find_production_callers(name)
        if callers:
            print(f"  ✅ {name:38s} {shape}  {len(callers)} 处")
        else:
            orphans.append((name, source, shape))
            print(f"  ❌ {name:38s} {shape}  零生产调用点  (判例 {source})")

    print()
    if orphans:
        print(f"判据存在、无人调用：{len(orphans)} 个")
        print("逐个确认：已废弃则从清单删除，未接线则补上消费方。")
        return 1

    print(f"全部 {len(REGISTERED_CRITERIA)} 个判据均有生产调用点。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
