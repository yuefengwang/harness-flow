"""筛存活变异：现有测试抓不住的才有资格进基线（B0 验收 8）。"""
import os, sys, json
SB = os.environ.get("B0_SANDBOX", "/tmp/b0-sandbox")
sys.path.insert(0, SB)
from pathlib import Path
from sw_lib.probe import injector as I, baseline as B

P = Path(SB) / "repo/b0-target/pricing.py"
TARGET = Path(SB) / "repo/b0-target"
src = P.read_text()

rows = []
for op in ("M2", "M4"):
    for i, site in enumerate(I.find_sites(src, op)):
        backup = I.inject(P, site)
        try:
            sv = B.is_surviving(TARGET)
        finally:
            I.revert(backup)
        assert P.read_text() == src, "revert 失败，中止"
        rows.append({"op": op, "i": i, "lineno": site["lineno"],
                     "mut": f"{site['original']}->{site['mutated']}",
                     "surviving": sv.get("surviving"),
                     "status": sv.get("status"),
                     "failing": sv.get("failing_nodes") or []})
        r = rows[-1]
        mark = "存活" if r["surviving"] else ("未测量" if r["surviving"] is None else "被捕获")
        print(f"{op}[{i}] line{r['lineno']:3d} {r['mut']:8s} -> {mark}  {r['failing'][:2]}")

json.dump(rows, open("/tmp/b0_survival.json","w"), ensure_ascii=False, indent=2)
alive = [r for r in rows if r["surviving"] is True]
print(f"\n存活 {len(alive)} / 共 {len(rows)}")
