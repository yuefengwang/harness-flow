# B0 基线采集脚本（一次性，已执行完毕）

产出：`workspace/probe/baseline-2026-08-24.json`（只读，**不可重新生成**）
原始审查产出：`workspace/probe/baseline-2026-08-24-reviews/`（人工标注的依据）

## 为什么脚本里的路径都指向 /tmp/b0-sandbox

两条硬约束逼出来的：

1. **采集必须跑在审查侧原貌上。** 落盘时的工作树已含 A3/A4/A5
   （`fact_pack.py`、`review_graph.py`、多角色配置），在其上采集得到的
   不是对照组。故用 `git archive fc6a201 | tar -x -C /tmp/b0-sandbox`
   还原出 B0 落盘那一刻的代码状态：审查侧四件套与 A3/A4/A5 均不存在。
2. **不得在 harness 自身注入变异**（B0 的 9.2 第 1 条）。工作树 dirty 时
   `revert` 无法区分探针的变异与用户未提交的改动。故被测对象是沙盒里
   新建的独立项目 `repo/b0-target`（一段普通折扣计算业务代码 + 7 条
   只覆盖典型值、不覆盖边界的测试）。

沙盒已在采集后删除。要复现须先重建：

```bash
mkdir -p /tmp/b0-sandbox
git archive fc6a201 | tar -x -C /tmp/b0-sandbox
cp config/credentials.yaml /tmp/b0-sandbox/config/
# 再按 b0_capture.py 顶部注释重建 repo/b0-target
```

## 执行顺序

1. `b0_injector_meta.py` —— 注入器元测试。**未经此步不得采集**（B0 的 9.1）：
   验变异确实写入、diff 严格 1 增 1 删、注释与 docstring 逐字保留、
   语法未坏、`revert` 逐字节恢复（sha256 比对）。
2. `b0_survival_screen.py` —— 存活筛选。只有现有测试抓不住的变异才有资格
   进观测（验收 8）。实测 5 个 M2 + 2 个 M4 位点里 6 个存活，
   M4[0] 被 `test_negative_rejected` 捕获、已排除。
3. `b0_capture.py` —— 采集主体。2 个样本 x 3 轮真实 04-review。
4. `b0_archive.py` —— 人工标注 + 落盘。标注依据写在脚本顶部 docstring。

## ⚠️ 期望结果是「未检出」

`detection_rate == 0.0` 是**成功**，不是失败。看到非 0 应先怀疑脚本，
**不得**通过调整注入方式或 prompt 让基线变好看（B0 的 9.4）——
那会把一份不可重采的数据毁掉，且毁掉的方式是「看起来变好了」。

## 本次采集的实际发现

检出率确实是 0.0，但**并非因为审查者没看见**：六轮里六轮都在自然语言里
提到了被注入的行为变更，其中 M2-1 三轮都明确指出「代码与 docstring
语义相反」。缺陷被看见了，却只有 1/6 轮变成了阻断性 Route。

详见归档里的 `baseline_findings` 字段。
