# 阶段 04：评审 (Review)

## 零记忆评审 (Zero-Memory Review)
- [ ] 使用 **Claude Code** (`claude`) 在不带实现上下文的情况下评审代码差异。
- [ ] **Google 标准合规性:** 特别验证代码是否符合 [Google 代码评审标准](https://google.github.io/eng-practices/review/)。
  - 代码是否可维护？
  - 逻辑是否足够简单，让其他开发者也能理解？
  - 文档是否充分？

## 确定性验证
- [ ] `mvn clean verify` (完整构建 + 集成测试)
- [ ] 检查是否存在未判断 `isPresent()` 的 `Optional.get()`，以及资源泄漏。

## 手动 QA 清单
- [ ] 日志检查 (无异常堆栈信息)
- [ ] API 响应验证 (使用 Postman/Curl)

## 自审笔记
- 

## 最终批准
- [ ] 合并: `git checkout main && git merge feature/[名称]`
- [ ] 是否删除分支？ (是/否)
