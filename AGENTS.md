# harness-flow 工作约定

## 开发纪律（强制）

本仓库的开发纪律写在 `docs/design/DEV-PROTOCOL.md`，**动代码前先读它**。
它是项目自己定的、有判例支撑的约束，优先级高于任何通用习惯。

三条最容易违反的：

- 红绿六步不得跳步，红必须是**断言失败**，不是 ImportError。
- 报告用三态（✅ / ❌ / ❓）。`unavailable` 不算通过；未验证的一律进 ❓ 并说明原因。
- 连续修同一个问题时，判据定在循环外。一轮之后没有新的失败信息，就是在重掷骰子，停下来交给用户。

## 本仓库的特征性失败

这个项目的 bug 极少是"漏了一个 case"，绝大多数是下面两类。修 bug 前先判断属于哪一类。

1. **判据本身有毛病** —— 门禁量错了地方、错了阶段、把 `unavailable` 当通过。
   现象是「两边结论相反但都没说谎」，或者「优质产出被判为空转」。
   → 用 `harness-criterion-design` 技能。

2. **同一形状在别处还有** —— 修掉一个实例，兄弟实例留在原地，下次以另一副面孔复发。
   commit log 里已有「同一个坑的第四次出现」「同一个 bug 的第二次出现」这样的记录。
   → 用 `harness-same-shape-sweep` 技能。

判例库在 `docs/design/A0-state-integrity.md` 的 2.9.x 各节，以及各次 fix 的 commit body。
遇到眼熟的 bug 先查那里，别重新推导一遍。

## 技能

按场景使用，不必每次全用：

| 场景 | 技能 |
|---|---|
| 改 / 加 / 排查任何门禁、hook、check | `harness-criterion-design` |
| 已定位根因，宣布修完之前 | `harness-same-shape-sweep` |
| 写实现或修 bug 之前 | `test-driven-development` |
| 遇到报错、测试失败、行为异常 | `systematic-debugging` |
| 宣称完成 / 通过 / 修好之前 | `verification-before-completion` |
| 完成较大改动、合并之前 | `requesting-code-review` |

## 状态文件

`.state`、`facts/`、`STATUS.json` 是门禁的真相源，一律走 `stage_state` 的读写函数，
不手工编辑、不用 `sed` / `python -c` 绕过。

## 验证命令

```bash
python3 -m pytest -q          # 全量单元测试，读通过计数而不只看退出码
./sw state get <task> <stage> <key>   # 读任务状态
```

单元测试全绿不等于机制接通 —— 这在本仓库已发生五次。改动涉及机制接线时，
额外在 `workspace/tasks/<task>/` 的真实任务上走一遍。
