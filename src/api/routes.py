"""RESTful API 路由 — FastAPI"""
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel

from src.services.db import Database
from src.services.rag import RAGService
from src.services.file_parser import parse_file
from src.services.vectorstore import VectorStore
from src.services.indexer import index_knowledge_item, reindex_knowledge_item
from src.models.knowledge import KnowledgeItem
from src.models.chat import Conversation, ChatMessage
from src.api.auth import authenticate, create_token, decode_token, register_user
from src.api.deps import get_container
from src.core.container import AppContainer


# ---- Auth ----

auth_router = APIRouter(prefix="/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str
    password: str


class RegisterReq(BaseModel):
    username: str
    password: str


@auth_router.post("/register")
def api_register(req: RegisterReq, authorization: str = Header(default="")):
    from src.api.auth import get_users_db
    users = get_users_db()
    # First user can register without auth (bootstrap)
    if users:
        # Subsequent registrations require admin authentication
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "需要管理员认证才能注册新用户")
        user = decode_token(authorization[7:])
        if not user:
            raise HTTPException(401, "令牌无效或已过期")
    try:
        token = register_user(req.username, req.password)
        return {"access_token": token, "token_type": "bearer"}
    except ValueError as e:
        raise HTTPException(400, str(e))


@auth_router.post("/login")
def api_login(req: LoginReq):
    token = authenticate(req.username, req.password)
    if not token:
        raise HTTPException(401, "用户名或密码错误")
    return {"access_token": token, "token_type": "bearer"}


# ---- Dependency ----

def _check_auth(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未提供认证令牌")
    user = decode_token(authorization[7:])
    if not user:
        raise HTTPException(401, "令牌无效或已过期")
    return user


# ---- Knowledge ----

kb_router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(_check_auth)])
refs_router = APIRouter(prefix="/refs", tags=["refs"], dependencies=[Depends(_check_auth)])


class KnowledgeCreate(BaseModel):
    title: str
    content: str
    tags: list[str] = []
    source_type: str = "manual"
    file_type: str = "txt"


class KnowledgeUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None


class KnowledgeBatchExport(BaseModel):
    ids: list[str] = []
    tag: Optional[str] = None


class TagRelationReq(BaseModel):
    parent_tag: str
    child_tag: str


class PropertySchemaReq(BaseModel):
    scope_type: str
    scope_id: str = ""
    property_name: str
    property_type: str
    required: int = 0
    default_value: object = None
    choices: list | None = None
    constraints: dict | None = None


class QueryDSLReq(BaseModel):
    filter: dict
    limit: int = 100
    offset: int = 0
    sort: dict | None = None
    include_blocks: bool = False


class GraphTraverseReq(BaseModel):
    start_ids: list[str]
    start_type: str = "knowledge"
    max_depth: int = 2
    ref_types: list[str] | None = None


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
def search_knowledge(q: str, top_k: int = 10,
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
    return {"id": item_id, "message": "创建成功"}


@kb_router.put("/{item_id}")
def update_knowledge(item_id: str, data: KnowledgeUpdate,
                     container: AppContainer = Depends(get_container)):
    existing = container.db.get_knowledge(item_id)
    if not existing:
        raise HTTPException(404, "知识条目不存在")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if fields:
        blocks = fields["content"] if "content" in fields else container.file_graph_service.read_page(item_id).blocks
        container.file_graph_service.update_page(item_id, blocks, metadata=fields)
    if "content" in fields:
        _try_wiki_compile(item_id)
    return {"message": "更新成功"}


@kb_router.delete("/{item_id}")
def delete_knowledge(item_id: str, container: AppContainer = Depends(get_container)):
    existing = container.db.get_knowledge(item_id)
    if not existing:
        raise HTTPException(404, "知识条目不存在")
    container.file_graph_service.delete_page(item_id)
    return {"message": "删除成功"}


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


# ---- Phase 2: Graph / Tags / Properties ----

graph_router = APIRouter(prefix="/graph", tags=["graph"], dependencies=[Depends(_check_auth)])
tags_router = APIRouter(prefix="/tags", tags=["tags"], dependencies=[Depends(_check_auth)])
properties_router = APIRouter(prefix="/properties", tags=["properties"], dependencies=[Depends(_check_auth)])


@graph_router.get("/unified")
def get_unified_graph(
    include_blocks: bool = True,
    include_tags: bool = True,
    container: AppContainer = Depends(get_container),
):
    return container.unified_graph.build(include_blocks=include_blocks, include_tags=include_tags)


@graph_router.post("/traverse")
def traverse_graph(data: GraphTraverseReq, container: AppContainer = Depends(get_container)):
    return container.graph_traversal.traverse(
        start_ids=data.start_ids,
        start_type=data.start_type,
        max_depth=data.max_depth,
        ref_types=data.ref_types,
    )


@tags_router.post("/relations")
def create_tag_relation(data: TagRelationReq, container: AppContainer = Depends(get_container)):
    container.tag_hierarchy.add_relation(data.parent_tag, data.child_tag)
    return {"parent_tag": data.parent_tag, "child_tag": data.child_tag}


@tags_router.get("/hierarchy/{tag}")
def get_tag_hierarchy(tag: str, container: AppContainer = Depends(get_container)):
    return {
        "tag": tag,
        "ancestors": container.tag_hierarchy.ancestors(tag),
        "descendants": container.tag_hierarchy.descendants(tag),
    }


@properties_router.post("/schemas")
def upsert_property_schema(data: PropertySchemaReq, container: AppContainer = Depends(get_container)):
    from src.models.property_schema import PropertySchema
    schema = container.property_schema.upsert(PropertySchema(**data.model_dump()))
    return schema.to_row()


@properties_router.get("/schemas")
def list_property_schemas(scope_type: str, scope_id: str = "", container: AppContainer = Depends(get_container)):
    return {"schemas": [schema.to_row() for schema in container.property_schema._repo.list_for_scope(scope_type, scope_id)]}


@properties_router.get("/effective/{block_id}")
def get_effective_properties(block_id: str, container: AppContainer = Depends(get_container)):
    return {"block_id": block_id, "properties": container.effective_properties.refresh_block(block_id)}


# ---- Chat / RAG ----

chat_router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(_check_auth)])


class QuestionReq(BaseModel):
    question: str
    conversation_id: Optional[str] = None


@chat_router.post("/ask")
def ask_question(data: QuestionReq, container: AppContainer = Depends(get_container)):
    result = container.rag_pipeline.query(data.question)
    sources = _normalize_sources(result.get("sources", []))
    conv_id = data.conversation_id
    if not conv_id:
        conv = Conversation(title=data.question[:30])
        container.db.insert_conversation(conv.to_row())
        conv_id = conv.id
    user_msg = ChatMessage(conversation_id=conv_id, role="user", content=data.question)
    container.db.insert_message(user_msg.to_row())
    ai_msg = ChatMessage(
        conversation_id=conv_id,
        role="assistant",
        content=result["answer"],
        sources=sources,
        source_graph=result.get("source_graph", {"nodes": [], "edges": []}),
    )
    container.db.insert_message(ai_msg.to_row())
    return {
        "conversation_id": conv_id,
        "answer": result["answer"],
        "sources": sources,
        "source_graph": result.get("source_graph", {"nodes": [], "edges": []}),
    }


def _normalize_sources(sources: list[dict]) -> list[dict]:
    normalized = []
    for source in sources or []:
        metadata = source.get("metadata") or {}
        block_id = (
            source.get("block_id")
            or source.get("chunk_id")
            or metadata.get("block_id")
            or source.get("id")
        )
        knowledge_id = source.get("knowledge_id") or metadata.get("knowledge_id")
        snippet = source.get("snippet") or source.get("text") or source.get("content") or ""
        normalized.append({
            **source,
            "block_id": block_id,
            "knowledge_id": knowledge_id,
            "title": source.get("title") or metadata.get("title") or "",
            "snippet": snippet,
            "score": source.get("score", source.get("distance")),
        })
    return normalized


@chat_router.get("/conversations")
def list_conversations(limit: int = 50, container: AppContainer = Depends(get_container)):
    return {"conversations": container.db.list_conversations(limit=limit)}


@chat_router.get("/conversations/{conv_id}/messages")
def get_messages(conv_id: str, container: AppContainer = Depends(get_container)):
    return {"messages": container.db.get_messages(conv_id)}


# ---- Wiki ----

from src.services.wiki_compiler import try_wiki_compile as _try_wiki_compile


wiki_router = APIRouter(prefix="/wiki", tags=["wiki"], dependencies=[Depends(_check_auth)])

# Jobs router for async tasks
jobs_router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(_check_auth)])


class SaveAnswerReq(BaseModel):
    question: str
    answer: str
    source_ids: Optional[list[str]] = None


@wiki_router.post("/save-answer")
def save_answer(data: SaveAnswerReq):
    from src.utils.config import Config
    if not Config.get("wiki.enabled", False):
        raise HTTPException(400, "Wiki 功能未启用")
    from src.services.wiki_compiler import WikiCompiler
    compiler = WikiCompiler()
    page_id = compiler.save_answer(data.question, data.answer, data.source_ids)
    if page_id:
        return {"page_id": page_id, "message": "已保存为 Wiki 页面"}
    return {"message": "回答内容过短，未保存"}


@wiki_router.get("/pages")
def list_wiki_pages(status: Optional[str] = None, search: Optional[str] = None,
                    sort_by: str = "updated_at", limit: int = 50, offset: int = 0):
    pages = Database.list_wiki_pages(status=status, search=search, sort_by=sort_by, limit=limit, offset=offset)
    total = Database.count_wiki_pages(status=status)
    return {"pages": pages, "total": total}


@wiki_router.get("/pages/{page_id}")
def get_wiki_page(page_id: str):
    page = Database.get_wiki_page(page_id)
    if not page:
        raise HTTPException(404, "Wiki 页面不存在")
    page["links"] = Database.get_links_for_page(page_id)
    page["backlinks"] = Database.get_backlinks(page_id)
    return page


@wiki_router.delete("/pages/{page_id}")
def delete_wiki_page(page_id: str):
    Database.delete_wiki_page(page_id)
    return {"message": "删除成功"}


@wiki_router.post("/lint")
def run_wiki_lint():
    from src.utils.config import Config
    if not Config.get("wiki.enabled", False):
        raise HTTPException(400, "Wiki 功能未启用")
    from src.services.wiki_lint import WikiLint
    linter = WikiLint()
    return linter.run()


@wiki_router.get("/ops")
def list_wiki_ops(limit: int = 50):
    return {"ops": Database.list_wiki_ops(limit=limit)}


# ---- Wiki Workflow Endpoints ----
@wiki_router.post("/pages/{page_id}/submit-review")
def submit_for_review(page_id: str, operator: str = "system", comment: str = ""):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.submit_for_review(page_id, operator, comment)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message, "from_status": result.from_status, "to_status": result.to_status}


@wiki_router.post("/pages/{page_id}/approve")
def approve_page(page_id: str, operator: str = "system", comment: str = ""):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.approve(page_id, operator, comment)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message}


@wiki_router.post("/pages/{page_id}/reject")
def reject_page(page_id: str, operator: str = "system", comment: str = ""):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.reject(page_id, operator, comment)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message}


@wiki_router.post("/pages/{page_id}/deprecate")
def deprecate_page(page_id: str, operator: str = "system", comment: str = ""):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.deprecate(page_id, operator, comment)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message}


@wiki_router.get("/pages/{page_id}/workflow")
def get_workflow_history(page_id: str):
    from src.services.wiki_workflow import WikiWorkflow
    return {"history": WikiWorkflow.get_history(page_id)}


# ---- Wiki Version Endpoints ----
@wiki_router.get("/pages/{page_id}/versions")
def list_wiki_versions(page_id: str):
    return {"versions": Database.list_wiki_versions(page_id)}


@wiki_router.post("/pages/{page_id}/versions/{version}/restore")
def restore_wiki_version(page_id: str, version: int):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.restore_version(page_id, version)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message}


# ---- Async Jobs Endpoints ----
ALLOWED_JOB_TYPES = {"reindex_all", "wiki_compile", "wiki_lint", "wiki_site_generate"}


class JobCreateReq(BaseModel):
    job_type: str
    params: dict = {}
    priority: int = 1
    max_retries: int = 3


@jobs_router.post("")
def create_job(data: JobCreateReq):
    if data.job_type not in ALLOWED_JOB_TYPES:
        raise HTTPException(400, f"不支持的任务类型: {data.job_type}，允许的类型: {', '.join(sorted(ALLOWED_JOB_TYPES))}")
    from src.services.async_task import AsyncTaskService
    job_id = AsyncTaskService.create_job(data.job_type, data.params, data.priority, data.max_retries)
    return {"job_id": job_id, "status": "pending"}


@jobs_router.get("")
def list_jobs(status: str = None, job_type: str = None, limit: int = 50, offset: int = 0):
    from src.services.async_task import AsyncTaskService
    jobs = AsyncTaskService.list_jobs(status, job_type, limit, offset)
    return {"jobs": [j.__dict__ for j in jobs]}


@jobs_router.get("/stats")
def get_job_stats():
    from src.services.async_task import AsyncTaskService
    return AsyncTaskService.get_stats()


@jobs_router.get("/{job_id}")
def get_job(job_id: str):
    from src.services.async_task import AsyncTaskService
    job = AsyncTaskService.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return {"job": job.__dict__}


@jobs_router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    from src.services.async_task import AsyncTaskService
    success = AsyncTaskService.cancel_job(job_id)
    if not success:
        raise HTTPException(400, "无法取消该任务")
    return {"message": "任务已取消"}


@jobs_router.delete("/{job_id}")
def delete_job(job_id: str):
    from src.services.async_task import AsyncTaskService
    success = AsyncTaskService.delete_job(job_id)
    if not success:
        raise HTTPException(400, "只能删除已完成/失败的任务")
    return {"message": "任务已删除"}


# ---- Phase 3: Query DSL ----

query_router = APIRouter(prefix="/query", tags=["query"], dependencies=[Depends(_check_auth)])


@query_router.post("")
def execute_structured_query(data: QueryDSLReq, container: AppContainer = Depends(get_container)):
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    dsl = data.model_dump(exclude_none=True)
    spec = QuerySpec.from_json(dsl)
    executor = QueryExecutor(db=container.db)
    results = executor.execute(spec)
    return {"results": results, "total": len(results)}


@query_router.post("/explain")
def explain_structured_query(data: QueryDSLReq, container: AppContainer = Depends(get_container)):
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json(data.model_dump(exclude_none=True))
    explainer = QueryExplainer()
    return explainer.explain(spec)
