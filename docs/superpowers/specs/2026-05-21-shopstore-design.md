# ShopStore — 简单商城应用设计规格

> 创建日期: 2026-05-21
> 状态: 草案

## 1. 概述

ShopStore 是一个标准电商应用，提供商品浏览、购物车、下单和订单管理功能，附带用户注册登录系统和模拟支付流程。

## 2. 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Java 21 (Records, Sealed Classes, Virtual Threads) |
| 框架 | Spring Boot 3 (Spring MVC, Spring Data JPA, Spring Security, Spring Validation) |
| 视图 | Thymeleaf + HTMX (异步交互) + Alpine.js (轻量状态管理) |
| 数据库 | H2 (开发/演示) → 可切换 PostgreSQL (生产) |
| 构建 | Gradle (Kotlin DSL) |
| 架构 | Hexagonal Architecture (Domain → Application → Infrastructure) |

## 3. 架构设计

### 3.1 分层结构

```
┌─────────────────────────────────────────────┐
│           Infrastructure (Web)               │
│   Controllers · Security · DTOs             │
├─────────────────────────────────────────────┤
│            Application Layer                 │
│   Use Cases (Services) · Port Interfaces    │
├─────────────────────────────────────────────┤
│             Domain Layer                     │
│   Product · User · Cart · Order · ValueObj  │
├─────────────────────────────────────────────┤
│       Infrastructure (Persistence)           │
│   JPA Repositories · Entities · Mappers     │
└─────────────────────────────────────────────┘
```

### 3.2 数据流

```
Browser (Thymeleaf + HTMX)
    ↓ HTTP GET/POST
Controller (接收 DTO, 调用 Service, 返回 ModelAndView/HX-Response)
    ↓ 调用 Port 接口
Application Service (编排用例逻辑)
    ↓ 调用 Port 接口
Repository (JPA, 操作 Entity)
    ↓ SQL
H2 Database
```

### 3.3 包结构

```
com.shopstore
├── domain/
│   ├── product/         Product, Category (Record)
│   ├── user/            User, Address (Record)
│   ├── cart/            Cart, CartItem (Record)
│   └── order/           Order, OrderItem, PaymentStatus (Enum)
├── application/
│   ├── port/
│   │   ├── inbound/     Service 接口
│   │   └── outbound/    Repository 接口
│   └── service/         用例实现 (@Service)
├── infrastructure/
│   ├── persistence/     JPA Entity, Repository 实现, Mapper
│   ├── web/             Spring MVC Controller
│   │   ├── dto/         请求/响应 DTO
│   │   └── controller/  Controller 类
│   ├── security/        Spring Security 配置 (form login, session)
│   └── payment/         模拟支付实现
└── bootstrap/
    ├── ShopStoreApplication.java
    └── WebConfig.java
```

## 4. 功能规格

### v1 严格范围

| 模块 | 功能 | 优先级 |
|------|------|--------|
| **用户** | 注册（用户名/密码/邮箱），登录/登出（Session），个人信息查看与编辑 | P0 |
| **商品** | 商品列表（分页+分类筛选），商品详情页面 | P0 |
| **购物车** | 添加/删除/修改数量，购物车页面展示，未登录不可用（提示登录） | P0 |
| **订单** | 从购物车下单，订单列表（按用户），订单详情，取消订单（仅未支付） | P0 |
| **支付** | 模拟支付页面（点击确认即完成，无真实网关对接） | P0 |

### 非功能需求

- 页面渲染服务端完成（Thymeleaf），HTMX 处理局部刷新
- 响应式设计（Bootstrap 5 CDN 或自备 minimal CSS）
- Session 管理用户登录态
- 数据校验（服务端 JSR-380 + 前端提示）

## 5. 数据模型设计

### 5.1 Domain Records

```java
// User
public record User(
    Long id, String username, String password, String email,
    String name, String phone, String address,
    LocalDateTime createdAt
) {}

// Product
public record Product(
    Long id, String name, String description,
    BigDecimal price, String imageUrl, String category,
    Integer stock, LocalDateTime createdAt
) {}

// Cart / CartItem
public record Cart(Long id, Long userId) {}
public record CartItem(Long id, Long cartId, Long productId, Integer quantity) {}

// Order
public record Order(
    Long id, Long userId, OrderStatus status,
    BigDecimal totalAmount, LocalDateTime createdAt
) {}
public record OrderItem(Long id, Long orderId, Long productId, String productName, 
                        BigDecimal price, Integer quantity) {}

public enum OrderStatus { PENDING, PAID, SHIPPED, DELIVERED, CANCELLED }
```

### 5.2 核心关系

```
User 1 ── * Cart           (一个用户一个购物车)
Cart 1 ── * CartItem       (购物车包含多个条目)
User 1 ── * Order          (一个用户多个订单)
Order 1 ── * OrderItem     (订单包含多个条目)
OrderItem * ── 1 Product   (订单条目关联商品)
```

## 6. 页面路由设计

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 首页（商品列表） |
| `/products` | GET | 商品列表（支持分类/?category=xxx&page=0） |
| `/products/{id}` | GET | 商品详情 |
| `/auth/register` | GET/POST | 用户注册 |
| `/auth/login` | GET/POST | 用户登录 |
| `/auth/logout` | POST | 用户登出 |
| `/cart` | GET | 购物车页面 |
| `/cart/add/{productId}` | POST | 添加到购物车 |
| `/cart/update/{itemId}` | POST | 修改购物车条目数量 |
| `/cart/remove/{itemId}` | POST | 删除购物车条目 |
| `/orders` | GET | 订单列表 |
| `/orders/create` | POST | 创建订单（从购物车） |
| `/orders/{id}` | GET | 订单详情 |
| `/orders/{id}/cancel` | POST | 取消订单 |
| `/payment/{orderId}` | GET | 支付页面 |
| `/payment/{orderId}/pay` | POST | 模拟支付确认 |

## 7. 安全设计

- Spring Security Form Login
- Session 管理用户状态
- 未登录用户只能访问 首页/商品列表/商品详情/登录注册
- 购物车和订单页面需要认证
- 密码使用 BCrypt 加密存储
- CSRF 保护启用

## 8. 错误处理

- 全局异常处理 `@ControllerAdvice`
- 404 → 自定义错误页面
- 500 → 错误页面
- 字段校验失败 → 返回表单并显示错误信息

## 9. 测试策略

| 层级 | 技术 | 范围 |
|------|------|------|
| 单元测试 | JUnit 5 + Mockito | Domain 逻辑、Application Service |
| 集成测试 | @SpringBootTest + @WebMvcTest | Controller、Repository |
| UI 测试 | Playwright（可选，v2） | E2E 用户流程 |

## 10. 非需求（明确排除的 v1 范围）

- ❌ 真实支付网关对接（Stripe/支付宝/微信支付）
- ❌ 管理后台（商品上架/订单管理后台）
- ❌ 商品搜索（全文检索）
- ❌ 多语言/国际化
- ❌ 商品评价/评分
- ❌ 优惠券/促销活动
- ❌ OAuth2 第三方登录
- ❌ 邮件/短信通知
- ❌ 商品图片上传（使用占位图 URL）
- ❌ Docker 容器化（后续可按需添加 Dockerfile 和 docker-compose.yml）

## 11. 演化规划（v1 → v2+）

参见 `EVOLUTION.md`
