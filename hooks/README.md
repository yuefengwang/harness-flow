# Hooks — 任务生命周期强制规则

每个 stage 在执行前/中/后触发对应的 hooks。Hooks 是强制执行的检查点，不可跳过。

## 阶段内 Hooks

按 stage 组织：

```
hooks/
├── README.md            # 本文件
├── 01-brainstorming.md  # 头脑风暴阶段 hooks
├── 02-planning.md       # 规划阶段 hooks
├── 03-coding.md         # 编码阶段 hooks
├── 04-review.md         # 评审阶段 hooks
└── 05-archive.md        # 归档阶段 hooks
```

## 跨阶段规则

以下规则适用于所有需要向用户提问的场景：

### 交互式提问 (Interactive Questioning)

| 规则 | 说明 |
|------|------|
| **必须交互式** | 禁止开放式提问 ("怎么做？")，必须提供选项让用户选择 |
| **优质选项** | 每个选项附带简短理由，标注推荐项 |
| **消除歧义** | 选项覆盖关键分歧点，减少用户猜测 |
| **反模式** | 选项无差异 / 选项掩盖真正歧义 / 自由输入代替选择 |

> 具体格式见 `hooks/01-brainstorming.md` 的 hook-01-02。

## Hook 格式

每个 hook 包含：
- **ID** — 唯一标识
- **触发时机** — pre / during / post
- **规则** — 必须执行的操作
- **验证** — 如何确认已满足

## 与模板的关系

- **模板** (`workflow/templates/`) — 描述性指引，提供结构、上下文、范例
- **Hooks** (`hooks/`) — 强制规则，定义不可跳过的检查点和操作

AI Agent 执行每个阶段时：先读模板了解结构，再按 hooks 逐条校验。
