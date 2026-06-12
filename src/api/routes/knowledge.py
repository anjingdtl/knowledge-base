from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer
from src.models.knowledge import KnowledgeItem
from src.services.indexer import index_knowledge_item, reindex_knowledge_item
from src.services.vectorstore import VectorStore


def _content_preview(text, max_len=200):
    if not text:
        return ""
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _op_log(container, operation, target_type, target_id, operator="api",
            before=None, after=None, metadata=None):
    try:
        container.operation_log.log(
            operation=operation, target_type=target_type, target_id=target_id,
            operator=operator, source="api",
            before=before, after=after, metadata=metadata,
        )
    except Exception:
        pass

kb_router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(_check_auth)])
refs_router = APIRouter(prefix="/refs", tags=["refs"], dependencies=[Depends(_check_auth)])


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=10_000_000)
    tags: list[str] = []
    source_type: str = "manual"
    file_type: str = "txt"


class KnowledgeUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None


class KnowledgeBatchExport(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=500)
    tag: Optional[str] = None


class UrlImportReq(BaseModel):
    url: str = Field(pattern=r"^https?://")
    tags: list[str] = []


@kb_router.get("")
def list_knowledge(
    tag: Optional[str] = None,
    file_type: Optional[str] = None,
    sort_by: str = Query("updated_at", pattern="^(updated_at|created_at|title)$"),
    sort_order: str = Query("DESC", pattern="^(ASC|DESC)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    container: AppContainer = Depends(get_container),
):
    offset = (page - 1) * page_size
    items = container.db.list_knowledge(tag=tag, file_type=file_type, sort_by=sort_by,
                                         sort_order=sort_order, limit=page_size, offset=offset)
    total = container.db.count_knowledge(tag=tag)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@kb_router.get("/search")
def search_knowledge(q: str, top_k: int = Query(10, ge=1, le=100),
                     container: AppContainer = Depends(get_container)):
    results = container.search_service.search(q, top_k=top_k)
    return {"results": results, "total": len(results)}


@kb_router.get("/tags")
def get_tags(container: AppContainer = Depends(get_container)):
    return {"tags": container.db.get_all_tags()}


@kb_router.get("/{item_id}")
def get_knowledge(item_id: str, container: AppContainer = Depends(get_container)):
    item = container.db.get_knowledge(item_id)
    if not item:
        raise HTTPException(404, "知识条目不存在")
    return item


@kb_router.get("/{item_id}/blocks")
def get_knowledge_blocks(item_id: str,
                         limit: int = Query(1000, ge=1, le=5000),
                         offset: int = Query(0, ge=0),
                         container: AppContainer = Depends(get_container)):
    item = container.db.get_knowledge(item_id)
    if not item:
        raise HTTPException(404, "Knowledge item not found")
    repo = container.block_repo
    blocks = repo.list_by_page(item_id, limit=limit, offset=offset)
    return {
        "page_id": item_id,
        "total": repo.count_by_page(item_id),
        "blocks": [_block_to_api(block) for block in blocks],
    }


@kb_router.post("", status_code=201)
def create_knowledge(data: KnowledgeCreate, container: AppContainer = Depends(get_container)):
    item_id = container.file_graph_service.create_page(
        data.title,
        data.content,
        tags=data.tags,
        metadata={"source_type": data.source_type, "file_type": data.file_type},
    )
    _try_wiki_compile(item_id)
    _op_log(container, "create", "knowledge", item_id, after={
        "title": data.title, "tags": data.tags,
        "source_type": data.source_type, "file_type": data.file_type,
    })
    return {"id": item_id, "message": "创建成功"}


@kb_router.post("/import", status_code=202)
async def import_file(
    file: UploadFile = File(...),
    tags: str = Form(""),
    container: AppContainer = Depends(get_container),
):
    import json
    import re
    import uuid

    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename or "upload.bin").name)
    upload_dir = Path(container.config.get_data_dir()) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / f"{uuid.uuid4().hex}-{safe_name}"
    with upload_path.open("wb") as target:
        while chunk := await file.read(1024 * 1024):
            target.write(chunk)

    parsed_tags = json.loads(tags) if tags.strip().startswith("[") else [
        tag.strip() for tag in tags.split(",") if tag.strip()
    ]
    job_id = container.db.create_job(
        "file_ingest",
        {"file_path": str(upload_path), "tags": parsed_tags},
    )
    return {"id": job_id, "job_id": job_id, "status": "pending", "type": "file_ingest"}


@kb_router.post("/import-url", status_code=202)
def import_url(data: UrlImportReq, container: AppContainer = Depends(get_container)):
    job_id = container.db.create_job("url_ingest", {"url": data.url, "tags": data.tags})
    return {"id": job_id, "job_id": job_id, "status": "pending", "type": "url_ingest"}


@kb_router.put("/{item_id}")
def update_knowledge(item_id: str, data: KnowledgeUpdate,
                     container: AppContainer = Depends(get_container)):
    existing = container.db.get_knowledge(item_id)
    if not existing:
        raise HTTPException(404, "知识条目不存在")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return {"message": "未提供需要更新的字段"}
    changes = {}
    import json as _json
    for k, v in fields.items():
        old_val = existing.get(k)
        if isinstance(old_val, str) and k == "tags":
            try:
                old_val = _json.loads(old_val)
            except Exception:
                pass
        if k == "content":
            changes["content"] = {"before": _content_preview(old_val or ""), "after": _content_preview(v or "")}
        else:
            changes[k] = {"before": old_val, "after": v}
    blocks = fields["content"] if "content" in fields else container.file_graph_service.read_page(item_id).blocks
    container.file_graph_service.update_page(item_id, blocks, metadata=fields)
    if "content" in fields:
        _try_wiki_compile(item_id)
    updated = container.db.get_knowledge(item_id) or {}
    _op_log(container, "update", "knowledge", item_id,
            before={k: v["before"] for k, v in changes.items()},
            after={k: v["after"] for k, v in changes.items()})
    return {
        "message": "更新成功",
        "updated_fields": list(fields.keys()),
        "changes": changes,
        "version": updated.get("version"),
    }


@kb_router.delete("/{item_id}")
def delete_knowledge(item_id: str, container: AppContainer = Depends(get_container)):
    existing = container.db.get_knowledge(item_id)
    if not existing:
        raise HTTPException(404, "知识条目不存在")
    import json as _json
    deleted_tags = existing.get("tags", "[]")
    if isinstance(deleted_tags, str):
        try:
            deleted_tags = _json.loads(deleted_tags)
        except Exception:
            deleted_tags = []
    deleted_item = {
        "title": existing.get("title", ""),
        "tags": deleted_tags,
        "content_preview": _content_preview(existing.get("content", "")),
    }
    _op_log(container, "delete", "knowledge", item_id, before=deleted_item)
    container.file_graph_service.delete_page(item_id)
    return {
        "message": "删除成功",
        "deleted_item": deleted_item,
        "version": existing.get("version"),
    }


@kb_router.get("/{item_id}/versions")
def list_versions(item_id: str, container: AppContainer = Depends(get_container)):
    return {"versions": container.db.list_versions(item_id)}


@kb_router.post("/{item_id}/versions/{version}/restore")
def restore_version(item_id: str, version: int,
                    container: AppContainer = Depends(get_container)):
    container.db.restore_version(item_id, version)
    return {"message": f"已恢复至版本 {version}"}


@kb_router.post("/export")
def export_knowledge(data: KnowledgeBatchExport,
                     container: AppContainer = Depends(get_container)):
    if data.ids:
        items = [container.db.get_knowledge(iid) for iid in data.ids]
        items = [i for i in items if i]
    elif data.tag:
        items = container.db.list_knowledge(tag=data.tag, limit=1000)
    else:
        items = container.db.list_knowledge(limit=1000)
    return {"items": items, "count": len(items)}


def _block_to_api(block):
    row = block.to_row()
    row["properties"] = block.properties
    return row


@refs_router.get("")
def list_entity_refs(
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    container: AppContainer = Depends(get_container),
):
    repo = container.entity_ref_repo
    refs = repo.list_refs(
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        limit=limit,
    )
    return {"refs": [ref.to_row() for ref in refs], "total": len(refs)}


def _try_wiki_compile(item_id: str):
    from src.services.wiki_compiler import try_wiki_compile
    try_wiki_compile(item_id)
