#!/usr/bin/env python3
"""把旧的 `key: value` 文本格式 `.state` 一次性迁移为 JSON。

背景：`read_state` 曾在读路径上自动识别旧格式并回写迁移。那条捷径的代价是
**把损坏合法化** —— 一份写到一半的 JSON 也会被当成旧格式解析成功然后固化
（详见 docs/design/A0-state-integrity.md 的 2.1）。D0-1 把迁移移出读路径，
挪到这里显式执行。

用法：
    python3 scripts/migrate_state_format.py            # 只报告，不改动
    python3 scripts/migrate_state_format.py --apply    # 实际写入

安全性：`--apply` 前会把原文件备份为 `.state.bak-<时间戳>`。
**不迁移无法确定为旧格式的内容** —— 拿不准的一律跳过并报告，
由人判断。静默猜测正是本次要消除的行为。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sw_lib.core.config import TASKS  # noqa: E402


def classify(text: str) -> str:
    """判定内容形态：json / empty / legacy / unknown。

    legacy 的判定刻意严格：每一个非空行都必须是 `key: value` 形态。
    只要有一行不符合，就归入 unknown 交给人处理 —— 半截 JSON 正是这样被挡住的
    （它的首行 `{` 不含冒号）。
    """
    stripped = text.strip()
    if not stripped:
        return "empty"
    try:
        json.loads(stripped)
        return "json"
    except json.JSONDecodeError:
        pass
    if stripped.startswith("{") or stripped.startswith("["):
        # 看起来想当 JSON 但解析失败 → 损坏，不是旧格式
        return "unknown"
    for line in stripped.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            return "unknown"
    return "legacy"


def parse_legacy(text: str) -> dict:
    out = {}
    for line in text.strip().splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, val = line.partition(":")
        out[key.strip()] = val.strip().strip('"')
    if "stage_idx" in out:
        try:
            out["stage_idx"] = int(out["stage_idx"])
        except (ValueError, TypeError):
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="实际写入（默认只报告）")
    args = ap.parse_args()

    if not TASKS.is_dir():
        print(f"任务目录不存在: {TASKS}")
        return 0

    counts = {"json": 0, "empty": 0, "legacy": 0, "unknown": 0}
    migrated, problems = [], []

    for sf in sorted(TASKS.glob("*/.state")):
        try:
            text = sf.read_text(encoding="utf-8")
        except OSError as e:
            problems.append((sf, f"读取失败: {e}"))
            continue

        kind = classify(text)
        counts[kind] += 1

        if kind == "unknown":
            problems.append((sf, "无法归类 —— 可能是损坏的 JSON，需人工检查"))
            continue
        if kind != "legacy":
            continue

        data = parse_legacy(text)
        if not data:
            problems.append((sf, "识别为旧格式但解析结果为空"))
            continue

        if args.apply:
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            sf.with_suffix(f".bak-{stamp}").write_text(text, encoding="utf-8")
            sf.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        migrated.append(sf)

    print(f"扫描 {sum(counts.values())} 个 .state: "
          f"json={counts['json']} empty={counts['empty']} "
          f"legacy={counts['legacy']} unknown={counts['unknown']}")
    for sf in migrated:
        print(f"  {'已迁移' if args.apply else '待迁移'}: {sf}")
    for sf, why in problems:
        print(f"  ⚠️  {sf}: {why}")
    if migrated and not args.apply:
        print("\n以上为预演。加 --apply 实际写入。")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
