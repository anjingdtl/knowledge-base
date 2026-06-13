import json
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer

settings_router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(_check_auth)],
)


class ModelSettingsUpdate(BaseModel):
    model: str | None = None
    api_base: str | None = None


class SettingsUpdate(BaseModel):
    llm: ModelSettingsUpdate | None = None
    embedding: ModelSettingsUpdate | None = None
    reranker: ModelSettingsUpdate | None = None


class McpSettingsUpdate(BaseModel):
    write_policy: str = Field(
        pattern="^(preview_only|local_confirm|token_required|disabled)$"
    )
    allow_http_write: bool = False


def _model_settings(config, section: str) -> dict:
    return {
        "model": config.get(f"{section}.model", ""),
        "api_base": config.get(f"{section}.base_url", ""),
        "api_key_set": bool(config.get(f"{section}.api_key", "")),
    }


@settings_router.get("")
def get_settings(container: AppContainer = Depends(get_container)):
    config = container.config
    return {
        "llm": _model_settings(config, "llm"),
        "embedding": _model_settings(config, "embedding"),
        "reranker": _model_settings(config, "reranker"),
        "mcp": {
            "write_policy": config.get("mcp.write_policy", "preview_only"),
            "allow_http_write": bool(config.get("mcp.allow_http_write", False)),
            "bind_host": config.get("mcp.bind_host", "127.0.0.1"),
        },
        "graph_backend": {
            "type": config.get("graph_backend.type", "sqlite"),
            "uri": config.get("graph_backend.uri", ""),
            "user": config.get("graph_backend.user", ""),
            "password_set": bool(config.get("graph_backend.password", "")),
        },
    }


@settings_router.post("")
def update_settings(data: SettingsUpdate, container: AppContainer = Depends(get_container)):
    config = container.config
    for section in ("llm", "embedding", "reranker"):
        values = getattr(data, section)
        if values is None:
            continue
        if values.model is not None:
            config.set(f"{section}.model", values.model)
        if values.api_base is not None:
            config.set(f"{section}.base_url", values.api_base)
    config.save()
    container.embedding.reset_client()
    container.llm.reset_client()
    return {"message": "设置已保存"}


@settings_router.post("/mcp")
def update_mcp_settings(data: McpSettingsUpdate, container: AppContainer = Depends(get_container)):
    container.config.set("mcp.write_policy", data.write_policy)
    container.config.set("mcp.allow_http_write", data.allow_http_write)
    container.config.save()
    return {"message": "MCP 安全设置已保存"}


@settings_router.post("/backup")
def create_backup(container: AppContainer = Depends(get_container)):
    backup_dir = Path(container.config.get_data_dir()) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"knowledge-{datetime.now():%Y%m%d-%H%M%S}.db"
    source = container.db.get_conn()
    with sqlite3.connect(backup_path) as target:
        source.backup(target)
    return {"message": "备份已创建", "path": str(backup_path)}


@settings_router.get("/export")
def export_data(container: AppContainer = Depends(get_container)):
    db = container.db
    payload = {
        "exported_at": datetime.now().isoformat(),
        "knowledge": db.list_knowledge(limit=100000),
        "wiki_pages": db.list_wiki_pages(limit=100000),
        "conversations": db.list_conversations(limit=100000),
        "agent_memory": container.agent_memory_repo.list_all(limit=100000),
    }
    return JSONResponse(
        content=json.loads(json.dumps(payload, ensure_ascii=False, default=str)),
        headers={"Content-Disposition": "attachment; filename=knowledge-backup.json"},
    )
