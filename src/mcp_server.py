"""ShineHeKnowledge MCP Server — 将知识库服务暴露为 MCP 工具

安全说明：MCP 工具通过 stdio 或本地 HTTP 传输运行，信任调用方（如 Claude Desktop）。
所有写操作（create/update/delete/wiki_*）不做额外认证，依赖 MCP 传输层的信任模型。
REST API 层（routes.py）则需要 Bearer Token 认证。
"""
import asyncio
import functools
import json
import logging
from contextlib import asynccontextmanager

WIKI_SEARCH_LIMIT = 3  # Wiki 结构化知识搜索结果上限，可通过配置覆盖
logger = logging.getLogger(__name__)

from fastmcp import FastMCP

from src.models.knowledge import KnowledgeItem
from src.services.db import Database
from src.services.mcp_heartbeat import beat
from src.services.rag import RAGService
from src.services.file_parser import parse_file, parse_url
from src.services.indexer import index_knowledge_item
from src.services.vectorstore import VectorStore
from src.utils.config import Config
from src.version import VERSION


# ---- 心跳后台任务 ----

_heartbeat_task: asyncio.Task | None = None


async def _heartbeat_loop():
    """每 10 秒写一次心跳，确保 GUI 能感知 MCP 服务存活"""
    while True:
        beat()
        await asyncio.sleep(10)


# ---- Lifespan ----

@asynccontextmanager
async def server_lifespan(server: FastMCP):
    global _heartbeat_task
    Config.load()
    Database.connect()
    beat()
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    yield {}
    _heartbeat_task.cancel()
    Database.close()


mcp = FastMCP(
    name="ShineHeKnowledge",
    version=VERSION,
    lifespan=server_lifespan,
)


# ---- 心跳装饰器 ----

def _heartbeat(fn):
    """为 MCP 工具函数添加心跳记录"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        beat()
        return fn(*args, **kwargs)
    return wrapper


# ---- Tools ----

from src.services.wiki_compiler import try_wiki_compile as _try_wiki_compile

@mcp.tool(
    description="基于语义相似度搜索知识库。使用向量嵌入查找与查询含义最相关的知识条目。Wiki 结构化知识优先返回。",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
@_heartbeat
def search(query: str, top_k: int = 5) -> list[dict]:
    """基于语义的向量搜索，查找与查询含义最相关的知识内容。

    Args:
        query: 搜索查询文本，支持自然语言描述
        top_k: 返回结果数量，默认5条
    """
    return _do_search(query, top_k)


def _do_search(query: str, top_k: int) -> list[dict]:
    """同步执行的搜索逻辑：向量 + FTS 合并去重"""
    output = []
    seen_kids = set()

    # Wiki 结构化知识优先
    try:
        wiki_results = Database.search_wiki_fts(query, limit=WIKI_SEARCH_LIMIT)
        for wr in wiki_results:
            summary = wr.get("concept_summary", "")
            content_preview = (wr.get("content", "") or "")[:300]
            output.append({
                "source": "wiki",
                "title": wr["title"],
                "summary": summary,
                "text": f"[Wiki] {wr['title']}: {summary}\n{content_preview}",
                "score": wr.get("fts_rank", 0),
            })
    except Exception as e:
        logger.warning("Wiki FTS search failed: %s", e)

    # 向量搜索
    try:
        vec_results = VectorStore().search(query, top_k=top_k)
        for r in vec_results:
            kid = (r.get("metadata") or {}).get("knowledge_id", "")
            if kid:
                seen_kids.add(kid)
            item = Database.get_knowledge(kid) if kid else None
            output.append({
                "source": "knowledge",
                "chunk_id": r["id"],
                "knowledge_id": kid,
                "title": item["title"] if item else "未知",
                "text": r["text"],
                "score": r["distance"],
            })
    except Exception as e:
        logger.warning("Vector search failed: %s", e)

    # FTS 补充搜索（合并去重：补充向量搜索未命中的知识条目）
    try:
        fts_results = Database.search_knowledge(query, limit=top_k)
        for item in fts_results:
            kid = item.get("id", "")
            if kid not in seen_kids:
                seen_kids.add(kid)
                output.append({
                    "source": "knowledge_fts",
                    "knowledge_id": kid,
                    "title": item["title"],
                    "text": (item.get("content", "") or "")[:500],
                    "score": item.get("fts_rank", 0),
                })
    except Exception as e:
        logger.warning("FTS search failed: %s", e)

    return output


@mcp.tool(
    description="基于关键词的全文搜索（FTS5）。适用于精确匹配关键词的场景。Wiki 结构化知识优先返回。",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
@_heartbeat
def search_fulltext(query: str, limit: int = 20, offset: int = 0) -> list[dict]:
    """使用 FTS5 全文索引搜索知识库。

    Args:
        query: 搜索关键词
        limit: 返回结果数量上限，默认20
        offset: 分页偏移量，默认0
    """
    output = []

    # Wiki 结构化知识优先
    wiki_results = Database.search_wiki_fts(query, limit=3)
    for wr in wiki_results:
        summary = wr.get("concept_summary", "")
        content_preview = (wr.get("content", "") or "")[:300]
        output.append({
            "source": "wiki",
            "title": wr["title"],
            "summary": summary,
            "text": f"[Wiki] {wr['title']}: {summary}\n{content_preview}",
        })

    # FTS5 知识搜索
    kb_results = Database.search_knowledge(query, limit=limit, offset=offset)
    for item in kb_results:
        item["source"] = "knowledge"
        output.append(item)

    return output


@mcp.tool(
    description="向知识库提问，使用 RAG（检索增强生成）流程自动检索相关内容并生成回答。",
)
@_heartbeat
def ask(question: str) -> dict:
    """基于知识库的智能问答，返回回答和引用来源。

    Args:
        question: 用户的问题
    """
    return _do_ask(question)


def _do_ask(question: str) -> dict:
    rag = RAGService()
    return rag.query(question)


@mcp.tool(
    description="创建新的知识条目。自动将内容分块并向量化索引，支持纯文本、Markdown 和代码。",
)
@_heartbeat
def create(
    title: str,
    content: str,
    tags: list[str] | None = None,
    file_type: str = "txt",
    source_type: str = "manual",
) -> dict:
    """创建一条知识并自动建立向量索引。

    Args:
        title: 知识标题
        content: 知识内容（支持纯文本、Markdown、代码）
        tags: 标签列表
        file_type: 内容类型 - txt（纯文本）、md（Markdown）、code（代码）
        source_type: 来源类型 - manual（手动）、file（文件）、web（网页）
    """
    tags = tags or []
    # 哈希去重
    import hashlib
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = Database.get_knowledge_by_hash(content_hash)
    if existing:
        return {"id": existing["id"], "title": existing["title"], "message": "内容已存在，跳过导入"}

    item = KnowledgeItem(
        title=title, content=content, tags=tags,
        source_type=source_type, file_type=file_type,
        content_hash=content_hash,
    )
    Database.insert_knowledge(item.to_row())
    index_knowledge_item(item)
    # Wiki 编译
    _try_wiki_compile(item.id)
    return {"id": item.id, "title": item.title, "message": "知识创建成功并已完成索引"}


@mcp.tool(
    description="根据 ID 获取指定知识条目的完整信息。",
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
@_heartbeat
def read(item_id: str) -> dict:
    """读取指定 ID 的知识条目。

    Args:
        item_id: 知识条目的唯一 ID
    """
    item = Database.get_knowledge(item_id)
    if not item:
        raise ValueError(f"知识条目不存在: {item_id}")
    return item


@mcp.tool(
    description="更新已有知识条目的标题、内容或标签。修改内容时会自动创建版本快照。",
)
@_heartbeat
def update(
    item_id: str,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """更新指定知识条目。

    Args:
        item_id: 要更新的知识条目 ID
        title: 新标题（可选）
        content: 新内容（可选）
        tags: 新标签列表（可选）
    """
    existing = Database.get_knowledge(item_id)
    if not existing:
        raise ValueError(f"知识条目不存在: {item_id}")
    fields = {}
    if title is not None:
        fields["title"] = title
    if content is not None:
        fields["content"] = content
    if tags is not None:
        fields["tags"] = json.dumps(tags, ensure_ascii=False)
    if not fields:
        return {"message": "未提供需要更新的字段"}
    Database.update_knowledge(item_id, **fields)
    return {"message": "知识更新成功", "updated_fields": list(fields.keys())}


@mcp.tool(
    description="删除指定的知识条目及其所有关联数据。此操作不可逆。",
    annotations={"destructiveHint": True},
)
@_heartbeat
def delete(item_id: str) -> dict:
    """删除指定 ID 的知识条目。

    Args:
        item_id: 要删除的知识条目 ID
    """
    existing = Database.get_knowledge(item_id)
    if not existing:
        raise ValueError(f"知识条目不存在: {item_id}")
    VectorStore().delete_by_knowledge(item_id)
    Database.delete_knowledge(item_id)
    return {"message": "知识删除成功", "id": item_id}


@mcp.tool(
    description="重建所有知识条目的索引（向量索引、全文索引、分块索引）。当搜索结果异常时使用。",
)
@_heartbeat
def reindex_all() -> dict:
    """重建全部知识条目的索引。包括分块、向量化和全文索引。"""
    from src.services.indexer import reindex_all as _reindex_all
    return _reindex_all()


@mcp.tool(
    description="列出知识库中的知识条目，支持按标签、文件类型筛选，分页和排序。",
    annotations={"readOnlyHint": True},
)
@_heartbeat
def list_knowledge(
    tag: str | None = None,
    file_type: str | None = None,
    sort_by: str = "updated_at",
    sort_order: str = "DESC",
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """列出知识条目，支持筛选和分页。

    Args:
        tag: 按标签筛选（可选）
        file_type: 按文件类型筛选（可选）
        sort_by: 排序字段 - updated_at、created_at、title
        sort_order: 排序方向 - DESC 或 ASC
        limit: 每页数量，默认20
        offset: 分页偏移量，默认0
    """
    items = Database.list_knowledge(
        tag=tag, file_type=file_type, sort_by=sort_by,
        sort_order=sort_order, limit=limit, offset=offset,
    )
    total = Database.count_knowledge(tag=tag)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@mcp.tool(
    description="获取知识库中所有已使用的标签列表。",
    annotations={"readOnlyHint": True},
)
@_heartbeat
def tags() -> list[str]:
    """返回知识库中所有标签的排序列表。"""
    return Database.get_all_tags()


@mcp.tool(
    description="解析本地文件并将其内容导入知识库。支持 PDF、DOCX、TXT、Markdown、HTML、图片及代码文件。Excel 文件的每个工作表独立导入。",
)
@_heartbeat
def ingest_file(file_path: str, tags: list[str] | None = None) -> dict:
    """解析本地文件并创建知识条目。

    Args:
        file_path: 本地文件的绝对路径
        tags: 要附加的标签列表
    """
    return _do_ingest_file(file_path, tags)


def _do_ingest_file(file_path: str, tags: list[str] | None = None) -> dict:
    tags = tags or []
    parsed_list = parse_file(file_path)

    import hashlib
    import os
    from datetime import datetime

    # 读取文件创建时间戳
    file_created_at = ""
    try:
        file_created_at = datetime.fromtimestamp(
            os.path.getctime(file_path)
        ).isoformat()
    except OSError:
        pass

    # 读取文件修改时间戳
    file_modified_at = ""
    try:
        file_modified_at = datetime.fromtimestamp(
            os.path.getmtime(file_path)
        ).isoformat()
    except OSError:
        pass

    file_size = 0
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        pass

    results = []
    for parsed in parsed_list:
        content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
        existing = Database.get_knowledge_by_hash(content_hash)
        if existing:
            results.append({
                "id": existing["id"],
                "title": existing["title"],
                "file_type": existing.get("file_type", ""),
                "message": "内容已存在，跳过导入",
            })
            continue

        item = KnowledgeItem(
            title=parsed.title,
            content=parsed.content,
            tags=tags,
            source_type="file",
            source_path=parsed.source_path,
            file_type=parsed.file_type,
            file_size=file_size,
            content_hash=content_hash,
            file_created_at=file_created_at,
            file_modified_at=file_modified_at,
        )
        Database.insert_knowledge(item.to_row())
        index_knowledge_item(item)
        _try_wiki_compile(item.id)
        results.append({
            "id": item.id,
            "title": item.title,
            "file_type": item.file_type,
            "message": "文件解析并索引成功",
        })

    if len(results) == 1:
        return results[0]
    return {
        "message": f"共导入 {len(results)} 个工作表",
        "sheets": results,
    }


@mcp.tool(
    description="解析网页 URL 并将其内容导入知识库。支持 HTTP/HTTPS 网页，自动提取正文文本。",
)
@_heartbeat
def ingest_url(url: str, tags: list[str] | None = None) -> dict:
    """抓取网页并创建知识条目。

    Args:
        url: 要导入的网页 URL（HTTP 或 HTTPS）
        tags: 要附加的标签列表
    """
    return _do_ingest_url(url, tags)


def _do_ingest_url(url: str, tags: list[str] | None = None) -> dict:
    tags = tags or []
    parsed = parse_url(url)

    import hashlib
    content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
    existing = Database.get_knowledge_by_hash(content_hash)
    if existing:
        return {
            "id": existing["id"],
            "title": existing["title"],
            "file_type": existing.get("file_type", ""),
            "message": "网页内容已存在，跳过导入",
        }

    item = KnowledgeItem(
        title=parsed.title,
        content=parsed.content,
        tags=tags,
        source_type="web",
        source_path=parsed.source_path,
        file_type=parsed.file_type,
        content_hash=content_hash,
    )
    Database.insert_knowledge(item.to_row())
    index_knowledge_item(item)
    _try_wiki_compile(item.id)
    return {
        "id": item.id,
        "title": item.title,
        "file_type": item.file_type,
        "message": "网页解析并索引成功",
    }


@mcp.tool(
    description="将好的问答回答保存为 Wiki 页面，实现知识沉淀和复利增长。",
)
@_heartbeat
def save_to_wiki(question: str, answer: str, source_ids: list[str] | None = None) -> dict:
    """将问答保存为 Wiki 页面。

    Args:
        question: 用户的问题
        answer: AI 的回答
        source_ids: 引用的知识条目 ID 列表
    """
    if not Config.get("wiki.enabled", False):
        return {"error": "Wiki 功能未启用"}
    from src.services.wiki_compiler import WikiCompiler
    compiler = WikiCompiler()
    page_id = compiler.save_answer(question, answer, source_ids)
    if page_id:
        return {"page_id": page_id, "message": "回答已保存为 Wiki 页面"}
    return {"message": "回答内容过短，未达到保存阈值"}


@mcp.tool(
    description="对知识库 Wiki 执行健康检查，找出孤立页面、过时信息和损坏链接。",
)
@_heartbeat
def wiki_lint() -> dict:
    """运行 Wiki 体检，返回健康报告。"""
    if not Config.get("wiki.enabled", False):
        return {"error": "Wiki 功能未启用"}
    from src.services.wiki_lint import WikiLint
    linter = WikiLint()
    report = linter.run()
    return report


# ---- Wiki Workflow MCP Tools ----

@mcp.tool(description="提交 Wiki 页面进行审核（draft -> review）")
def wiki_submit_review(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """提交页面审核"""
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.submit_for_review(page_id, operator, comment)
    return {"success": result.success, "message": result.message}


@mcp.tool(description="审批通过 Wiki 页面（review -> published）")
def wiki_approve(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """审批通过"""
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.approve(page_id, operator, comment)
    return {"success": result.success, "message": result.message}


@mcp.tool(description="驳回 Wiki 页面（review -> draft）")
def wiki_reject(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """驳回页面"""
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.reject(page_id, operator, comment)
    return {"success": result.success, "message": result.message}


@mcp.tool(description="弃用 Wiki 页面（published -> deprecated）")
def wiki_deprecate(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """弃用页面"""
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.deprecate(page_id, operator, comment)
    return {"success": result.success, "message": result.message}


@mcp.tool(description="获取 Wiki 页面工作流历史")
def wiki_workflow_history(page_id: str) -> dict:
    """获取工作流历史"""
    from src.services.wiki_workflow import WikiWorkflow
    history = WikiWorkflow.get_history(page_id)
    return {"history": history}


@mcp.tool(description="获取 Wiki 页面版本列表")
def wiki_list_versions(page_id: str) -> dict:
    """列出页面所有版本"""
    versions = Database.list_wiki_versions(page_id)
    return {"versions": versions}


@mcp.tool(description="恢复到指定版本的 Wiki 页面")
def wiki_restore_version(page_id: str, version: int) -> dict:
    """恢复到指定版本"""
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.restore_version(page_id, version)
    return {"success": result.success, "message": result.message}


# ---- Async Jobs MCP Tools ----

@mcp.tool(description="创建异步任务")
def create_async_job(job_type: str, params: dict = None, priority: int = 1, max_retries: int = 3) -> dict:
    """创建异步任务"""
    from src.services.async_task import AsyncTaskService
    job_id = AsyncTaskService.create_job(job_type, params or {}, priority, max_retries)
    return {"job_id": job_id, "status": "pending"}


@mcp.tool(description="获取异步任务状态")
def get_async_job(job_id: str) -> dict:
    """获取任务状态"""
    from src.services.async_task import AsyncTaskService
    job = AsyncTaskService.get_job(job_id)
    if not job:
        return {"error": "任务不存在"}
    return job.__dict__


@mcp.tool(description="列出异步任务")
def list_async_jobs(status: str = None, job_type: str = None, limit: int = 20) -> dict:
    """列出任务"""
    from src.services.async_task import AsyncTaskService
    jobs = AsyncTaskService.list_jobs(status, job_type, limit)
    return {"jobs": [j.__dict__ for j in jobs]}


@mcp.tool(description="取消异步任务")
def cancel_async_job(job_id: str) -> dict:
    """取消任务"""
    from src.services.async_task import AsyncTaskService
    success = AsyncTaskService.cancel_job(job_id)
    return {"success": success, "message": "任务已取消" if success else "无法取消"}


# ---- Resources ----

@mcp.resource("kb://knowledge/{item_id}")
def get_knowledge_resource(item_id: str) -> str:
    """获取指定知识条目的完整内容。"""
    item = Database.get_knowledge(item_id)
    if not item:
        raise ValueError(f"知识条目不存在: {item_id}")
    return json.dumps(item, ensure_ascii=False, indent=2)


@mcp.resource("kb://tags")
def get_tags_resource() -> str:
    """获取知识库中所有标签。"""
    tags = Database.get_all_tags()
    return json.dumps({"tags": tags, "count": len(tags)}, ensure_ascii=False, indent=2)


@mcp.resource("kb://stats")
def get_stats_resource() -> str:
    """获取知识库统计信息。"""
    return json.dumps({
        "knowledge_items": Database.count_knowledge(),
        "vector_chunks": VectorStore().count(),
        "tags": len(Database.get_all_tags()),
    }, ensure_ascii=False, indent=2)


# ---- Prompts ----

@mcp.prompt(name="kb_qa", description="知识库问答提示模板")
def knowledge_qa_prompt(question: str) -> str:
    return (
        "你是一个专业的知识库助手。请基于知识库中的内容准确回答用户问题。"
        "回答时请标注引用的知识来源，如果知识库中没有相关信息请明确说明。\n\n"
        f"用户问题：{question}"
    )
