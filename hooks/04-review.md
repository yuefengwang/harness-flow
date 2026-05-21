# 04-Review Hooks

## hook-04-01: Impact Analysis
- **When**: during
- **Rule**: assess side effects on other components
- **Check**: API contract? Performance? Upstream/downstream?

## hook-04-02: Security Audit
- **When**: during
- **Rule**: no secrets, injection risk, or missing validation
- **Check**: all inputs validated, no hardcoded keys

## hook-04-03: Zero-Memory Review
- **When**: during
- **Rule**: can a newcomer understand the diff without context?
- **Check**: self-explanatory names and commit messages

## hook-04-04: Full Build
- **When**: post
- **Rule**: full CI pipeline must pass
- **Check**: `mvn verify` / `npm test` / equivalent → green

## hook-04-05: Deliverable Consistency
- **When**: post
- **Rule**: code, test, doc, config in sync
- **Check**: README / task.yaml reflect latest state

## hook-04-06: Review Decision & Reroute Gate
- **When**: post (before stage exit)
- **Rule**: review must classify outcome into exactly one routing path. **Route 决策必须通过 ask_user 工具与用户交互确认** — agent 不得自行填写 Route 值，必须向用户汇报审查结论并请求选择路由方向。
- **Decision Tree**:

```
04-Review 完成
    │
    ├─ [前进] 全部门禁通过
    │   代码完整、构建通过、测试绿色、安全合规、与 spec/plan 一致
    │   └─ ✅  Route → 05-Archive (正常归档)
    │
    ├─ [返工] 代码层问题
    │   条件(任一):
    │   │  • 计划任务未全部实现 (完成度 < 100%)
    │   │  • 构建/测试失败
    │   │  • 代码质量缺陷 (模式错误、技术债)
    │   │  • 安全漏洞 (实现在代码中)
    │   │  • 与设计规格不一致 (spec drift)
    │   └─ 🔄 Route → 03-Coding (代码修正)
    │
    ├─ [返工] 规划层问题
    │   条件(任一):
    │   │  • 架构设计根本性缺陷
    │   │  • 技术选型不可行 (依赖缺失/版本冲突)
    │   │  • 重要任务遗漏导致无法交付
    │   │  • 资源/时间估算严重偏差
    │   └─ 🔄 Route → 02-Planning (规划修订)
    │
    └─ [返工] 需求层问题
       条件(任一):
        • 需求在中途发生实质性变更
        • 设计决策被用户推翻
        └─ 🔄 Route → 01-Brainstorming (需求重审)
```

- **Check**: review 文档中必须明确记录路由路径及理由

## hook-04-07: Reroute Evidence (返工证据链)
- **When**: post (仅在路由非 05-Archive 时触发)
- **Rule**: 每次返工路由必须附带可复现的证据
- **Evidence per route**:
  - **→ 03-Coding**: 具体列出缺失的任务/失败的测试/缺陷的文件及行号
  - **→ 02-Planning**: 具体列出架构缺陷/遗漏任务/不可行的技术方案及替代建议
  - **→ 01-Brainstorming**: 具体列出变更的需求点和需要重新决策的设计项
- **Check**: review 文档的 AI Output 中包含 Evidence 表格，无空泛描述
