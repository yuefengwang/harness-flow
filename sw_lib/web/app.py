"""
sw_lib.web.app — FastAPI 应用工厂

提供 create_app() 工厂函数，统一配置 Jinja2 模板、静态文件挂载和路由注册。
"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes.tasks import router as tasks_router
from .routes.console import router as console_router

HERE = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    app = FastAPI(title="Harness-Flow Dashboard", version="0.1.0")

    # 挂载静态文件
    static_dir = HERE / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 注册路由
    app.include_router(tasks_router)
    app.include_router(console_router)

    return app
