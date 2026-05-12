# 阶段 03：编码 (Coding)

## 当前任务
*正在进行: [任务名称]*

## 🏗️ 实现标准
- **编译保证 (强制):** 只有在代码成功编译后，任务才算完成。如果 `mvn compile` 或等效的构建命令失败，你必须诊断并修复错误，继续执行任务直到构建成功。
- **设计模式:** 对于涉及复杂分支、多种实现或状态管理的逻辑，**优先使用显式设计模式** (策略模式、工厂模式、观察者模式等)，避免深层嵌套的 `if/else` 或 `switch` 块。
- **Google 工程实践:** 严格遵守 [Google Java 编程风格指南](https://google.github.io/styleguide/javaguide.html) 和 [Google 工程最佳实践](https://google.github.io/eng-practices/)。
  - 有意义的命名。
  - **代码注释 (强制):** 所有的复杂逻辑、非直观的算法实现或关键业务决策必须配备清晰的注释。
  - 短小、专注的方法。
  - 为公共 API 提供详尽的 Javadoc。

## TDD 循环: 红-绿-重构 (RED-GREEN-REFACTOR)
- [ ] **红 (RED):** 为当前需求编写一个失败的 JUnit 测试。
- [ ] **绿 (GREEN):** 编写最少的代码使测试通过。
- [ ] **重构 (REFACTOR):** 优化导入、清理 Stream 逻辑、检查命名，同时保持测试为绿色。

## 实现细节
- *如果切换上下文，留给 Claude Code 或其他代理的笔记。*

## Git 规范
- [ ] 原子提交: `git commit -m "feat: [简短描述]"`
- [ ] 遵循项目 commit 命名规范。

## 验证检查
- [ ] `mvn test` (测试通过)
- [ ] `mvn checkstyle:check` (Google 风格合规性)
- [ ] 集成测试成功
