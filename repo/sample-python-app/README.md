# Sample Python App

基于 FastAPI 的示例 Python 项目。

## 技术栈
- Python 3.12
- FastAPI
- Pydantic v2
- Pytest / Hypothesis

## 快速开始
```bash
uv sync
uv run uvicorn src.main:app --reload
```

## 目录结构
```
src/        # 业务代码
tests/      # 测试代码
pyproject.toml
```

## API 端点
- `GET /api/health` — 健康检查
- `POST /api/items` — 创建条目
