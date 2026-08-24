"""注入器元测试（B0 的 9.1：未经元测试的注入器不得用于基线采集）。

验四条：变异确实写入、diff 严格 1 增 1 删、语法未坏、revert 逐字节恢复。
"""
import os, sys, subprocess, hashlib, difflib
SB = os.environ.get("B0_SANDBOX", "/tmp/b0-sandbox")
sys.path.insert(0, SB)
from pathlib import Path
from sw_lib.probe import injector as I

P = Path(SB) / "repo/b0-target/pricing.py"
before = P.read_text()
h_before = hashlib.sha256(before.encode()).hexdigest()

sites = I.find_sites(before, "M2")
site = sites[2]   # lineno 21: amount > VIP_THRESHOLD -> >=
print(f"位点: line {site['lineno']}  {site['original']} -> {site['mutated']}")

backup = I.inject(P, site)
after = P.read_text()

# 1. 变异确实写入
assert after != before, "❌ 变异未写入"
print("✅ 1 变异确实写入")

# 2. diff 严格 1 增 1 删（B0 验收 9 / A11 的 2.3：不得用 ast.unparse）
diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0))
adds = [l for l in diff if l.startswith("+") and not l.startswith("+++")]
dels = [l for l in diff if l.startswith("-") and not l.startswith("---")]
print(f"   diff: {len(adds)} 增 {len(dels)} 删")
assert (len(adds), len(dels)) == (1, 1), f"❌ diff 规模失控: {len(adds)}增{len(dels)}删"
print("✅ 2 diff 严格 1 增 1 删")

# 注释与 docstring 逐字保留
assert before.count('"""') == after.count('"""')
assert "这是业务方明确要求的" in after, "❌ 注释被吃掉"
print("✅ 2b 注释与 docstring 逐字保留")

# 3. 语法未坏
r = subprocess.run([sys.executable, "-c", f"import ast; ast.parse(open('{P}').read())"],
                   capture_output=True)
assert r.returncode == 0, f"❌ 语法坏了: {r.stderr.decode()}"
print("✅ 3 语法未坏")
print(f"   变异行: {adds[0]}")

# 4. revert 逐字节恢复（B0 验收 3）
assert I.revert(backup) is True
h_after_revert = hashlib.sha256(P.read_text().encode()).hexdigest()
assert h_after_revert == h_before, "❌ revert 未逐字节恢复"
print("✅ 4 revert 逐字节恢复")
print("\n注入器元测试全部通过 —— 可用于基线采集")
