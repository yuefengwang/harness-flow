# Sample Java App

基于 Spring Boot 3 的示例 Java 项目。

## 技术栈
- Java 21
- Spring Boot 3
- Maven
- JUnit 5 / Mockito

## 快速开始
```bash
./mvnw clean install
./mvnw spring-boot:run
```

## 目录结构
```
src/
  main/java/com/example/   # 业务代码
  test/java/com/example/   # 测试代码
pom.xml
```

## API 端点
- `GET /api/health` — 健康检查
- `GET /api/users` — 用户列表
