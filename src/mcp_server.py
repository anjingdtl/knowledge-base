"""ShineHeKnowledge MCP Server — 将知识库服务暴露为 MCP 工具

安全说明：MCP 工具通过 stdio 或本地 HTTP 传输运行，信任调用方（如 Claude Desktop）。
所有写操作（create/update/delete/wiki_*）不做额外认证，依赖 MCP 传输层的信任模型。
REST API 层（routes.py）则需要 Bearer Token 认证。
"""
import asyncio
import functools
import json
import logging
import os
from contextlib import asynccontextmanager

WIKI_SEARCH_LIMIT = 3  # Wiki 结构化知识搜索结果上限，可通过配置覆盖
logger = logging.getLogger(__name__)

from fastmcp import FastMCP

from src.core.container import create_container, shutdown_container, AppContainer
from src.models.knowledge import KnowledgeItem
from src.services.mcp_heartbeat import beat
from src.services.rag import RAGService
from src.services.file_parser import parse_file, parse_url
from src.services.indexer import index_knowledge_item
from src.utils.config import Config
from src.version import VERSION


# ---- 心跳后台任务 ----

_heartbeat_task: asyncio.Task | None = None

# ---- Container ----

_container: AppContainer | None = None


def _get_container() -> AppContainer:
    """获取 Container 实例（lifespan 未触发时延迟创建，主要用于测试）"""
    global _container
    if _container is None:
        _container = create_container()
    return _container


async def _heartbeat_loop():
    """每 10 秒写一次心跳，确保 GUI 能感知 MCP 服务存活"""
    while True:
        beat()
        await asyncio.sleep(10)


# ---- Lifespan ----

@asynccontextmanager
async def server_lifespan(server: FastMCP):
    global _heartbeat_task, _container
    _container = create_container()
    beat()
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    yield {}
    _heartbeat_task.cancel()
    shutdown_container(_container)


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


def _op_log(operation, target_type, target_id, operator="system", source="mcp",
            before=None, after=None, metadata=None):
    """便捷操作日志记录"""
    try:
        _get_container().operation_log.log(
            operation=operation, target_type=target_type, target_id=target_id,
            operator=operator, source=source,
            before=before, after=after, metadata=metadata,
        )
    except Exception:
        pass


def _content_preview(text, max_len=200):
    if not text:
        return ""
    return text[:max_len] + ("..." if len(text) > max_len else "")


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
    return _get_container().search_service.search(query, top_k=top_k)


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
    container = _get_container()
    db = container.db
    output = []

    # Wiki 结构化知识优先
    wiki_results = db.search_wiki_fts(query, limit=3)
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
    kb_results = db.search_knowledge(query, limit=limit, offset=offset)
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
    return _get_container().rag_pipeline.query(question)


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
    container = _get_container()
    db = container.db
    # 哈希去重
    import hashlib
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = db.get_knowledge_by_hash(content_hash)
    if existing:
        return {"id": existing["id"], "title": existing["title"], "message": "内容已存在，跳过导入"}

    item_id = container.file_graph_service.create_page(
        title,
        content,
        tags=tags,
        metadata={"source_type": source_type, "file_type": file_type},
    )
    # Wiki 编译
    _try_wiki_compile(item_id)
    item = db.get_knowledge(item_id) or {"title": title}
    _op_log("create", "knowledge", item_id, after={
        "title": item["title"], "content_preview": _content_preview(content),
        "tags": tags, "source_type": source_type, "file_type": file_type,
    })
    return {"id": item_id, "title": item["title"], "path": item.get("source_path", ""), "message": "知识创建成功并已完成索引"}


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
    item = _get_container().db.get_knowledge(item_id)
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
    dry_run: bool = False,
) -> dict:
    """更新指定知识条目。

    Args:
        item_id: 要更新的知识条目 ID
        title: 新标题（可选）
        content: 新内容（可选）
        tags: 新标签列表（可选）
        dry_run: 设为 True 时只预览变更不执行
    """
    container = _get_container()
    db = container.db
    existing = db.get_knowledge(item_id)
    if not existing:
        raise ValueError(f"知识条目不存在: {item_id}")
    fields = {}
    if title is not None:
        fields["title"] = title
    if content is not None:
        fields["content"] = content
    if tags is not None:
        fields["tags"] = tags
    if not fields:
        return {"message": "未提供需要更新的字段"}

    import json as _json
    changes = {}
    for k, v in fields.items():
        old_val = existing.get(k)
        if isinstance(old_val, str) and k == "tags":
            try:
                old_val = _json.loads(old_val)
            except Exception:
                pass
        if k == "content":
            changes["content"] = {
                "before": _content_preview(old_val or ""),
                "after": _content_preview(v or ""),
            }
        else:
            changes[k] = {"before": old_val, "after": v}

    if dry_run:
        return {"dry_run": True, "would_change": changes, "item_id": item_id}

    blocks = fields["content"] if "content" in fields else container.file_graph_service.read_page(item_id).blocks
    container.file_graph_service.update_page(item_id, blocks, metadata=fields)
    updated = db.get_knowledge(item_id) or {}
    _op_log("update", "knowledge", item_id, before={
        k: v["before"] for k, v in changes.items()
    }, after={
        k: v["after"] for k, v in changes.items()
    })
    return {
        "message": "知识更新成功",
        "updated_fields": list(fields.keys()),
        "changes": changes,
        "version": updated.get("version"),
    }


@mcp.tool(
    description="删除指定的知识条目及其所有关联数据。此操作不可逆。",
    annotations={"destructiveHint": True},
)
@_heartbeat
def delete(item_id: str, dry_run: bool = False) -> dict:
    """删除指定 ID 的知识条目。

    Args:
        item_id: 要删除的知识条目 ID
        dry_run: 设为 True 时只预览将删除的数据不执行
    """
    container = _get_container()
    existing = container.db.get_knowledge(item_id)
    if not existing:
        raise ValueError(f"知识条目不存在: {item_id}")

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
        "source_type": existing.get("source_type", ""),
        "file_type": existing.get("file_type", ""),
    }

    if dry_run:
        block_count = 0
        try:
            rows = container.db.get_conn().execute(
                "SELECT COUNT(*) as cnt FROM blocks WHERE page_id = ?", (item_id,)
            ).fetchone()
            block_count = rows["cnt"]
        except Exception:
            pass
        return {
            "dry_run": True,
            "would_delete": {**deleted_item, "block_count": block_count},
            "warning": "此操作不可逆",
        }

    _op_log("delete", "knowledge", item_id, before=deleted_item, metadata={
        "version": existing.get("version"),
    })
    container.file_graph_service.delete_page(item_id)
    return {
        "message": "知识删除成功",
        "id": item_id,
        "deleted_item": deleted_item,
        "version": existing.get("version"),
    }


@mcp.tool(
    description="重建所有知识条目的索引（向量索引、全文索引、分块索引）。当搜索结果异常时使用。",
)
@_heartbeat
def reindex_all(dry_run: bool = False) -> dict:
    """重建全部知识条目的索引。包括分块、向量化和全文索引。

    Args:
        dry_run: 设为 True 时只返回将重建的数量不执行
    """
    container = _get_container()
    count = container.db.count_knowledge()
    if dry_run:
        return {"dry_run": True, "would_reindex": count}
    _op_log("reindex", "system", "all", metadata={"count": count})
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
    db = _get_container().db
    items = db.list_knowledge(
        tag=tag, file_type=file_type, sort_by=sort_by,
        sort_order=sort_order, limit=limit, offset=offset,
    )
    total = db.count_knowledge(tag=tag)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@mcp.tool(
    description="获取知识库中所有已使用的标签列表。",
    annotations={"readOnlyHint": True},
)
@_heartbeat
def tags() -> list[str]:
    """返回知识库中所有标签的排序列表。"""
    return _get_container().db.get_all_tags()


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


def _validate_file_path(file_path: str) -> str:
    """验证文件路径在允许的目录范围内，防止路径遍历攻击。

    允许的目录包括:
    1. config.yaml 中 security.allowed_ingest_dirs 配置的目录
    2. SHINEHE_HOME 环境变量指向的目录
    3. 项目数据目录
    """
    from src.utils.paths import get_data_dir

    resolved = os.path.realpath(file_path)

    # 检查文件是否存在
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # 构建允许的目录白名单
    allowed_dirs: list[str] = []

    # 配置文件中的显式白名单
    configured_dirs = Config.get("security.allowed_ingest_dirs", [])
    if configured_dirs:
        for d in configured_dirs:
            allowed_dirs.append(os.path.realpath(d))

    # SHINEHE_HOME 或项目根目录
    env_root = os.environ.get("SHINEHE_HOME")
    if env_root:
        allowed_dirs.append(os.path.realpath(env_root))

    # 项目数据目录
    allowed_dirs.append(str(get_data_dir()))

    # 项目根目录（源码模式下）
    from src.utils.paths import get_project_root
    allowed_dirs.append(str(get_project_root()))

    # 用户主目录（作为常见导入来源）
    home = os.path.expanduser("~")
    allowed_dirs.append(os.path.realpath(home))

    # 检查文件是否在任一允许的目录下
    for allowed in allowed_dirs:
        if resolved.startswith(allowed + os.sep) or resolved == allowed:
            return resolved

    raise PermissionError(
        f"文件路径不在允许的目录范围内: {file_path}。"
        f"请在 config.yaml 的 security.allowed_ingest_dirs 中添加允许的目录。"
    )


def _do_ingest_file(file_path: str, tags: list[str] | None = None) -> dict:
    tags = tags or []

    # 安全校验：路径规范化 + 白名单验证
    validated_path = _validate_file_path(file_path)
    parsed_list = parse_file(validated_path)

    import hashlib
    from datetime import datetime, timezone

    # 读取文件创建时间戳（使用 UTC 时区避免 naive datetime）
    file_created_at = ""
    try:
        file_created_at = datetime.fromtimestamp(
            os.path.getctime(validated_path), tz=timezone.utc
        ).isoformat()
    except OSError:
        pass

    # 读取文件修改时间戳（使用 UTC 时区避免 naive datetime）
    file_modified_at = ""
    try:
        file_modified_at = datetime.fromtimestamp(
            os.path.getmtime(validated_path), tz=timezone.utc
        ).isoformat()
    except OSError:
        pass

    file_size = 0
    try:
        file_size = os.path.getsize(validated_path)
    except OSError:
        pass

    results = []
    container = _get_container()
    db = container.db
    for parsed in parsed_list:
        content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
        existing = db.get_knowledge_by_hash(content_hash)
        if existing:
            results.append({
                "id": existing["id"],
                "title": existing["title"],
                "file_type": existing.get("file_type", ""),
                "message": "内容已存在，跳过导入",
            })
            continue

        blocks = parsed.structured if parsed.structured else parsed.content
        item_id = container.file_graph_service.create_page(
            parsed.title,
            blocks,
            tags=tags,
            metadata={
                "source_type": "file",
                "source_path": parsed.source_path,
                "file_type": parsed.file_type,
                "file_created_at": file_created_at,
                "file_modified_at": file_modified_at,
            },
        )
        item = db.get_knowledge(item_id) or {"title": parsed.title, "file_type": parsed.file_type}
        _try_wiki_compile(item_id)
        _op_log("ingest", "knowledge", item_id, after={
            "title": item["title"], "file_type": item.get("file_type", parsed.file_type),
        }, metadata={"source": "file", "path": validated_path})
        results.append({
            "id": item_id,
            "title": item["title"],
            "file_type": item.get("file_type", parsed.file_type),
            "path": item.get("source_path", ""),
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
    container = _get_container()
    db = container.db

    import hashlib
    content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
    existing = db.get_knowledge_by_hash(content_hash)
    if existing:
        return {
            "id": existing["id"],
            "title": existing["title"],
            "file_type": existing.get("file_type", ""),
            "message": "网页内容已存在，跳过导入",
        }

    blocks = parsed.structured if parsed.structured else parsed.content
    item_id = container.file_graph_service.create_page(
        parsed.title,
        blocks,
        tags=tags,
        metadata={"source_type": "web", "source_path": parsed.source_path, "file_type": parsed.file_type},
    )
    item = db.get_knowledge(item_id) or {"title": parsed.title, "file_type": parsed.file_type}
    _try_wiki_compile(item_id)
    _op_log("ingest", "knowledge", item_id, after={
        "title": item["title"], "file_type": item.get("file_type", parsed.file_type),
    }, metadata={"source": "url", "url": url})
    return {
        "id": item_id,
        "title": item["title"],
        "file_type": item.get("file_type", parsed.file_type),
        "path": item.get("source_path", ""),
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
        _op_log("wiki_create", "wiki_page", page_id, after={
            "question": question[:100], "source_ids": source_ids,
        })
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
    if result.success:
        _op_log("workflow_transition", "wiki_page", page_id, operator=operator,
                before={"status": "draft"}, after={"status": "review"},
                metadata={"comment": comment})
    return {"success": result.success, "message": result.message}


@mcp.tool(description="审批通过 Wiki 页面（review -> published）")
def wiki_approve(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """审批通过"""
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.approve(page_id, operator, comment)
    if result.success:
        _op_log("workflow_transition", "wiki_page", page_id, operator=operator,
                before={"status": "review"}, after={"status": "published"},
                metadata={"comment": comment})
    return {"success": result.success, "message": result.message}


@mcp.tool(description="驳回 Wiki 页面（review -> draft）")
def wiki_reject(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """驳回页面"""
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.reject(page_id, operator, comment)
    if result.success:
        _op_log("workflow_transition", "wiki_page", page_id, operator=operator,
                before={"status": "review"}, after={"status": "draft"},
                metadata={"comment": comment})
    return {"success": result.success, "message": result.message}


@mcp.tool(description="弃用 Wiki 页面（published -> deprecated）")
def wiki_deprecate(page_id: str, operator: str = "system", comment: str = "") -> dict:
    """弃用页面"""
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.deprecate(page_id, operator, comment)
    if result.success:
        _op_log("workflow_transition", "wiki_page", page_id, operator=operator,
                before={"status": "published"}, after={"status": "deprecated"},
                metadata={"comment": comment})
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
    versions = _get_container().db.list_wiki_versions(page_id)
    return {"versions": versions}


@mcp.tool(description="恢复到指定版本的 Wiki 页面")
def wiki_restore_version(page_id: str, version: int) -> dict:
    """恢复到指定版本"""
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.restore_version(page_id, version)
    if result.success:
        _op_log("wiki_update", "wiki_page", page_id, after={"restored_version": version})
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


@mcp.tool()
def structured_query(query_dsl: str, limit: int = 100) -> str:
    """Execute a structured JSON DSL query against the knowledge base.

    The DSL supports tag, property, fulltext, link, file_type, source_type filters
    combined with and/or/not groups.

    Args:
        query_dsl: JSON string with the query DSL
        limit: Maximum results to return
    """
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    container = _get_container()
    try:
        dsl = json.loads(query_dsl) if isinstance(query_dsl, str) else query_dsl
        spec = QuerySpec.from_json(dsl)
        spec.limit = min(spec.limit, limit)
        executor = QueryExecutor(db=container.db)
        results = executor.execute(spec)
        return json.dumps(results, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def explain_query(query_dsl: str) -> str:
    """Explain a structured query: show human-readable summary, execution plan, and condition tree.

    Args:
        query_dsl: JSON string with the query DSL
    """
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    try:
        dsl = json.loads(query_dsl) if isinstance(query_dsl, str) else query_dsl
        spec = QuerySpec.from_json(dsl)
        explainer = QueryExplainer()
        return json.dumps(explainer.explain(spec), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def graph_traverse(start_ids: str, max_depth: int = 2, start_type: str = "knowledge") -> str:
    """Traverse the knowledge graph starting from given page/block IDs.

    Args:
        start_ids: JSON array of starting node IDs (e.g. '["page-id-1", "page-id-2"]')
        max_depth: Maximum traversal depth
        start_type: Type of start nodes (knowledge or block)
    """
    from src.services.graph_traversal import GraphTraversalService

    container = _get_container()
    try:
        ids = json.loads(start_ids) if isinstance(start_ids, str) else start_ids
        service = GraphTraversalService(db=container.db)
        result = service.traverse(start_ids=ids, start_type=start_type, max_depth=max_depth)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ---- Resources ----

@mcp.resource("kb://knowledge/{item_id}")
def get_knowledge_resource(item_id: str) -> str:
    """获取指定知识条目的完整内容。"""
    item = _get_container().db.get_knowledge(item_id)
    if not item:
        raise ValueError(f"知识条目不存在: {item_id}")
    return json.dumps(item, ensure_ascii=False, indent=2)


@mcp.resource("kb://tags")
def get_tags_resource() -> str:
    """获取知识库中所有标签。"""
    tags = _get_container().db.get_all_tags()
    return json.dumps({"tags": tags, "count": len(tags)}, ensure_ascii=False, indent=2)


@mcp.resource("kb://stats")
def get_stats_resource() -> str:
    """获取知识库统计信息。"""
    c = _get_container()
    try:
        chunk_count = c.block_store.count()
    except Exception:
        chunk_count = 0
    return json.dumps({
        "knowledge_items": c.db.count_knowledge(),
        "vector_chunks": chunk_count,
        "tags": len(c.db.get_all_tags()),
    }, ensure_ascii=False, indent=2)


# ---- Prompts ----

@mcp.tool(
    description="查询操作审计日志。可按目标类型、目标 ID、操作类型筛选。",
    annotations={"readOnlyHint": True},
)
@_heartbeat
def query_operation_logs(
    target_type: str | None = None,
    target_id: str | None = None,
    operation: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """查询操作日志。

    Args:
        target_type: 目标类型筛选 (knowledge/wiki_page/block/tag_relation)
        target_id: 目标 ID 筛选
        operation: 操作类型筛选 (create/update/delete/ingest/reindex/wiki_create/workflow_transition)
        source: 来源筛选 (mcp/api/gui)
        limit: 返回数量上限
        offset: 分页偏移量
    """
    logs = _get_container().operation_log.query(
        target_type=target_type, target_id=target_id,
        operation=operation, source=source,
        limit=limit, offset=offset,
    )
    return {"logs": logs, "count": len(logs)}


# ---- Prompts ----

@mcp.prompt(name="kb_qa", description="知识库问答提示模板")
def knowledge_qa_prompt(question: str) -> str:
    return (
        "你是一个专业的知识库助手。请基于知识库中的内容准确回答用户问题。"
        "回答时请标注引用的知识来源，如果知识库中没有相关信息请明确说明。\n\n"
        f"用户问题：{question}"
    )
