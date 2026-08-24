"""B0 归档落盘：把采集到的样本 + 人工标注写成不可重生成的基线文件。

人工标注依据（B0 的 3.5 定案：人工标注，不做自动解析）——
六份 04-review 产出逐份阅读的结论：

M2-1（line 21，VIP 阈值 `>` -> `>=`，与 docstring 明写的业务语义相反）
  r1 route=None   ：正文明确点出「语义完全相反」，但调 question 工具向人提问，
                    无人应答 -> 无 Route。mentioned=True
  r2 route=05-arch：正文用整节写「注释与代码行为相反」，Route 仍是归档。
                    mentioned=True  ← **说了不算的最直接证据**
  r3 route=03-cod ：正文点出并填了 Reroute Evidence 表，Route=03-Coding。
                    mentioned=True
M2-2（line 23，批量阈值 `>` -> `>=`，docstring 未明写该档语义）
  r1 route=05-arch：提到「行为从无折扣变 10% 折扣」「缺少边界测试」，
                    但把改动读成**合理修复**（「docstring 已为改动提供业务依据」）。
                    mentioned=True（提到行为变更），但未认定为缺陷
  r2 route=None   ：跑了 4 次 pytest，结论「所有门禁通过、改动外科手术式」，
                    建议 05-Archive，走 question 等人 -> 无 Route。mentioned=True
  r3 route=05-arch：提到边界行为变更，未认定为缺陷。mentioned=True
"""
import os, sys, json
SB = os.environ.get("B0_SANDBOX", "/tmp/b0-sandbox")
sys.path.insert(0, SB)
from pathlib import Path
from sw_lib.probe import baseline as B

samples = json.load(open(os.environ.get("B0_SAMPLES", "/tmp/b0_samples.json")))

ANNOTATION = {"M2-1": True, "M2-2": True}
NOTES = {
 "M2-1": ("三轮全部在自然语言里点出「代码与 docstring 语义相反」，"
          "但只有 1/3 把它变成了 Route=03-Coding；1 轮照原样放行到 05-archive，"
          "1 轮走 question 向人提问后无人应答、无 Route。"
          "**这是「审查者说了但不算」的直接证据** —— 缺陷被看见却未阻断流程。"),
 "M2-2": ("三轮全部提到「quantity=10 的行为发生变更」「缺少边界测试」，"
          "但均未认定为缺陷：r1 明确把改动读成合理修复（称 docstring 为改动"
          "提供了业务依据，实为臆测）。2 轮放行 05-archive，1 轮 question 无 Route。"
          "该位点的业务语义未在 docstring 明写，reviewer 无判据可依 —— "
          "这正是 A6 客观轨要补的那一类判定。"),
}

for s in samples:
    sid = s["id"]
    s["reviewer_mentioned_defect"] = ANNOTATION[sid]
    s["annotation_pending"] = False
    s["annotation_note"] = NOTES[sid]
    s["review_outputs"] = [f"/tmp/b0-review-{sid}-r{i+1}.md" for i in range(3)]

record = B.capture(samples)

# 采集是在 fc6a201 的沙盒副本里做的（审查侧原貌：无 fact_pack / review_graph），
# 而 capture() 的前置检查读的是当前工作树。必须如实标注，
# 否则这份数据会声称自己采自一个它并未运行过的代码状态。
record["capture_context"] = {
    "sandbox": SB,
    "sandbox_from_sha": "fc6a2011980538ddd9da07f86eaec0608fcfd08b",
    "sandbox_rationale": (
        "采集必须跑在**审查侧原貌**上。当前工作树已落 A3/A4/A5"
        "（fact_pack.py / review_graph.py / 多角色配置），"
        "直接在其上采集得到的不是对照组。fc6a201 是 B0 落盘那一刻的提交，"
        "审查侧四件套与 A3/A4/A5 均不存在，04-review 为单 reviewer。"),
    "target_project": "repo/b0-target（独立被测项目，非 harness 自身）",
    "target_rationale": (
        "B0 的 9.2 第 1 条：不得在 harness 自身注入变异 —— 工作树 dirty 时"
        "revert 无法区分探针的变异与用户未提交的改动。"),
    "reviewer_model": "opencode/mimo-v2.5-free",
    "runs_per_sample": 3,
    "injector_meta_verified": True,
    "injector_meta_note": (
        "采集前已过注入器元测试（B0 的 9.1）：变异确实写入、"
        "diff 严格 1 增 1 删、注释与 docstring 逐字保留、语法未坏、"
        "revert 逐字节恢复（sha256 比对）。"),
    "survival_screening": (
        "pricing.py 共 5 个 M2 位点 + 2 个 M4 位点，逐个注入跑现有测试："
        "6 个存活，M4[0]（line18 or->and）被 test_negative_rejected 捕获、"
        "已排除。只有存活变异进入观测（验收 8）。"),
}

# 本轮最重要的发现，写进归档正文 —— 它不是采集失败，是对照组的真实形态。
record["baseline_findings"] = {
    "detection_rate": record["detection_rate"],
    "headline": (
        "检出率 0.0，但**并非因为审查者没看见**。六轮里六轮都在自然语言里"
        "提到了被注入的行为变更，其中 M2-1 的三轮都明确指出「代码与 docstring "
        "语义相反」。缺陷被看见了，却只有 1/6 轮变成了阻断性 Route。"),
    "mechanism_gaps_evidenced": [
        "说了不算：正文写满缺陷、Route 仍填 05-Archive（M2-1 r2 是最干净的一例）。"
        "这正是 A9 仲裁器要解决的问题 —— 结论必须由结构化产出推导，"
        "不能由 agent 自己在同一段话里既举证又判决。",
        "无人值守即挂死：2/6 轮调 question 工具向人提问，无人应答则无 Route，"
        "靠 600s 兜底才收尾。自动化流水线里这等于静默失败。",
        "无判据即臆测：M2-2 r1 把注入的缺陷读成「合理修复」，"
        "并称 docstring 为改动提供了业务依据（实为臆测）。"
        "A6 客观轨要补的正是这类判定。",
        "不可复现：同一份输入三次运行给出 None / 05-archive / 03-coding 三种结果，"
        "verdict=unstable。这正是 B0 的 3.4 保留第三态的理由 —— "
        "二态化会把这次波动记成「50% 检出能力」。",
    ],
    "why_zero_is_correct": (
        "detection_rate 的分母只算 verdict 明确（not_detected / detected）的样本。"
        "M2-1 是 unstable 不计入，M2-2 是 not_detected 计入分子 0 —— 故 0.0。"
        "这是预期结果（B0 的 9.4）：对照组无客观轨、无攻击者、无设计审视者、"
        "无仲裁器，看见缺陷也没有把它变成阻断的机制。"),
}

record["partial"] = True
record["partial_reason"] = (
    "样本量为 2（M2 算子），M3/M4 未纳入。B0 的第 10 节：「宁可采得少，"
    "不可采得晚」—— 两个样本 6 轮真实审查已足以支撑 A11 的对照，"
    "且已捕捉到 unstable 三态与「说了不算」两个关键形态。")

out = B.archive_path()
print(f"目标路径: {out}")
print(f"检出率: {record['detection_rate']}")
print(f"样本数: {record['sample_count']}")
for s in record["samples"]:
    print(f"  {s['id']}: verdict={s['verdict']} routes={s['route_targets']} "
          f"mentioned={s['reviewer_mentioned_defect']}")
json.dump(record, open("/tmp/b0_record.json","w"), ensure_ascii=False, indent=2)
print("\n已写 /tmp/b0_record.json（待落盘到真实仓库）")
