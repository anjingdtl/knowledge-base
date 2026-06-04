"""FastAPI 依赖注入"""
from fastapi import HTTPException, Request
from src.core.container import AppContainer


def get_container(request: Request) -> AppContainer:
    """从 FastAPI app.state 获取 Container"""
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(status_code=503, detail="服务正在启动中")
    return container
