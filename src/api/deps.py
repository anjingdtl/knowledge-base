"""FastAPI 依赖注入"""
from fastapi import Request
from src.core.container import AppContainer


def get_container(request: Request) -> AppContainer:
    """从 FastAPI app.state 获取 Container"""
    return request.app.state.container
