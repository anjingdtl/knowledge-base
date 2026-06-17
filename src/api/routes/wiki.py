import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.deps import get_container
from src.api.routes.auth import _check_auth, _get_current_user
from src.core.container import AppContainer

wiki_router = APIRouter(prefix="/wiki", tags=["wiki"], dependencies=[Depends(_check_auth)])


class SaveAnswerReq(BaseModel):
    question: str
    answer: str
    source_ids: Optional[list[str]] = None


class FixDeadLinksReq(BaseModel):
    max_pages: int = Field(default=50, ge=1, le=200, description="最多处理多少个含死链的页面")
    dry_run: bool = Field(default=False, description="仅预览修复方案，不实际写入")


class ComplexRepairReq(BaseModel):
    action: str = Field(description="scan | mark | repair")
    issues: Optional[list[dict]] = Field(default=None, description="指定要修复/标记的问题列表（action=mark/repair时）")


class WikiPageWriteReq(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = ""


class WikiWorkflowReq(BaseModel):
    action: str
    comment: str = ""


@wiki_router.post("/save-answer")
def save_answer(data: SaveAnswerReq, container: AppContainer = Depends(get_container)):
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
def list_wiki_pages(
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "updated_at",
    limit: int = 50,
    offset: int = 0,
    container: AppContainer = Depends(get_container),
):
    pages = container.db.list_wiki_pages(status=status, search=search, sort_by=sort_by, limit=limit, offset=offset)
    total = container.db.count_wiki_pages(status=status)
    return {"pages": pages, "total": total}


@wiki_router.post("/pages", status_code=201)
def create_wiki_page(data: WikiPageWriteReq, container: AppContainer = Depends(get_container)):
    now = datetime.now().isoformat()
    page_id = str(uuid.uuid4())
    container.db.insert_wiki_page({
        "id": page_id,
        "title": data.title,
        "content": data.content,
        "source_ids": "[]",
        "tags": "[]",
        "concept_summary": "",
        "status": "draft",
        "lint_score": 1.0,
        "created_at": now,
        "updated_at": now,
    })
    return {"id": page_id, "message": "创建成功"}


@wiki_router.get("/pages/{page_id}")
def get_wiki_page(page_id: str, container: AppContainer = Depends(get_container)):
    page = container.db.get_wiki_page(page_id)
    if not page:
        raise HTTPException(404, "Wiki 页面不存在")
    page["links"] = container.db.get_links_for_page(page_id)
    page["backlinks"] = container.db.get_backlinks(page_id)
    return page


@wiki_router.put("/pages/{page_id}")
def update_wiki_page(
    page_id: str,
    data: WikiPageWriteReq,
    container: AppContainer = Depends(get_container),
):
    page = container.db.get_wiki_page(page_id)
    if not page:
        raise HTTPException(404, "Wiki 页面不存在")
    container.db.save_wiki_version(page_id, page)
    container.db.update_wiki_page(page_id, title=data.title, content=data.content)
    return {"message": "保存成功"}


@wiki_router.post("/pages/{page_id}/workflow")
def run_wiki_workflow(
    page_id: str,
    data: WikiWorkflowReq,
    operator: str = Depends(_get_current_user),
):
    from src.services.wiki_workflow import WikiWorkflow

    actions = {
        "submit_review": WikiWorkflow.submit_for_review,
        "approve": WikiWorkflow.approve,
        "reject": WikiWorkflow.reject,
        "deprecate": WikiWorkflow.deprecate,
        "republish": WikiWorkflow.republish,
    }
    action = actions.get(data.action)
    if action is None:
        raise HTTPException(400, f"不支持的工作流操作: {data.action}")
    result = action(page_id, operator, data.comment)
    if not result.success:
        raise HTTPException(400, result.message)
    return {
        "message": result.message,
        "from_status": result.from_status,
        "to_status": result.to_status,
    }


@wiki_router.delete("/pages/{page_id}")
def delete_wiki_page(page_id: str, container: AppContainer = Depends(get_container)):
    page = container.db.get_wiki_page(page_id)
    if not page:
        raise HTTPException(404, "Wiki 页面不存在")
    container.db.delete_wiki_page(page_id)
    return {"message": "已移至回收站", "page_id": page_id, "title": page.get("title", "")}


@wiki_router.post("/pages/{page_id}/purge")
def purge_wiki_page(page_id: str, container: AppContainer = Depends(get_container)):
    page = container.db.get_wiki_page(page_id)
    if not page:
        raise HTTPException(404, "Wiki 页面不存在")
    if page.get("status") != "deleted":
        raise HTTPException(400, "只能永久删除回收站中的页面")
    container.db.purge_wiki_page(page_id)
    return {"message": "已永久删除", "page_id": page_id}


@wiki_router.post("/pages/{page_id}/restore")
def restore_wiki_page(page_id: str, container: AppContainer = Depends(get_container)):
    page = container.db.get_wiki_page(page_id)
    if not page:
        raise HTTPException(404, "Wiki 页面不存在")
    if page.get("status") != "deleted":
        raise HTTPException(400, "只能恢复回收站中的页面")
    container.db.restore_wiki_page(page_id)
    return {"message": "已恢复为草稿", "page_id": page_id}


@wiki_router.post("/lint")
def run_wiki_lint(container: AppContainer = Depends(get_container)):
    from src.utils.config import Config
    if not Config.get("wiki.enabled", False):
        raise HTTPException(400, "Wiki 功能未启用")
    from src.services.wiki_lint import WikiLint
    linter = WikiLint()
    return linter.run()


@wiki_router.post("/fix-dead-links")
def fix_dead_links(data: FixDeadLinksReq, container: AppContainer = Depends(get_container)):
    """使用 LLM 智能修复 Wiki 页面中的 [[死链]]。

    - dry_run=true 时仅返回修复方案预览，不实际修改数据库
    - max_pages 控制单次最多处理页面数（默认 50），防止 LLM 调用过多
    """
    from src.utils.config import Config
    if not Config.get("wiki.enabled", False):
        raise HTTPException(400, "Wiki 功能未启用")
    from src.services.wiki_compiler import WikiCompiler
    compiler = WikiCompiler()
    if data.dry_run:
        # dry_run: 仅扫描死链 + stale，不调用 LLM
        from src.services.db import Database
        from src.services.wiki_compiler import _WIKI_LINK_RE
        pages = Database.list_wiki_pages(limit=500)
        if not pages:
            return {"status": "empty", "scanned": 0, "dead_links": [], "stale_pages": 0}
        all_titles = {p["title"] for p in pages}
        dead_links = []
        stale_pages = []
        for page in pages[:data.max_pages]:
            content = page.get("content", "") or ""
            for m in _WIKI_LINK_RE.finditer(content):
                ref = m.group(1).strip()
                if ref not in all_titles:
                    dead_links.append({
                        "source_page_id": page["id"],
                        "source_title": page["title"],
                        "dead_ref": ref,
                    })
            # stale 检查
            source_ids_raw = page.get("source_ids", "[]")
            try:
                import json as _json
                source_ids = _json.loads(source_ids_raw) if isinstance(source_ids_raw, str) else source_ids_raw
            except (_json.JSONDecodeError, TypeError):
                source_ids = []
            if source_ids:
                existing = Database.get_knowledge_batch(source_ids)
                deleted_ids = [sid for sid in source_ids if sid not in existing]
                if deleted_ids:
                    stale_pages.append({
                        "page_id": page["id"],
                        "page_title": page["title"],
                        "deleted_source_ids": deleted_ids,
                    })
        return {
            "status": "preview",
            "scanned": min(len(pages), data.max_pages),
            "total_dead_links": len(dead_links),
            "dead_links": dead_links,
            "stale_pages": len(stale_pages),
            "stale_details": stale_pages,
        }
    else:
        result = compiler.repair_dead_references(max_pages=data.max_pages)
        return result


@wiki_router.post("/complex-repair")
def complex_repair(data: ComplexRepairReq, container: AppContainer = Depends(get_container)):
    """复杂问题扫描/标记/修复（orphan/empty/duplicate/contradiction）。

    - action=scan: 扫描四类复杂问题 + 已标记待修复页面
    - action=mark: 仅标记为"复杂异常"，不修复
    - action=repair: 执行 LLM/规则修复
    """
    from src.utils.config import Config
    if not Config.get("wiki.enabled", False):
        raise HTTPException(400, "Wiki 功能未启用")
    from src.services.wiki_lint import WikiLint
    linter = WikiLint()

    if data.action == "scan":
        return linter.scan_complex_issues()

    elif data.action == "mark":
        # 标记页面为"复杂异常"，不修复
        marked = []
        issues = data.issues or []
        for issue in issues:
            pid = issue.get("page_id", "")
            cats = issue.get("categories", [])
            if pid and cats:
                WikiLint.mark_complex_anomaly(pid, cats)
                marked.append({"page_id": pid, "page_title": issue.get("page_title", ""), "marked": cats})
        return {"action": "mark", "marked_count": len(marked), "details": marked}

    elif data.action == "repair":
        result = linter.repair_complex_issues(issues=data.issues)
        return result

    else:
        raise HTTPException(400, f"不支持的 action: {data.action}，可选: scan/mark/repair")


@wiki_router.get("/ops")
def list_wiki_ops(limit: int = 50, container: AppContainer = Depends(get_container)):
    return {"ops": container.db.list_wiki_ops(limit=limit)}


@wiki_router.post("/pages/{page_id}/submit-review")
def submit_for_review(
    page_id: str,
    comment: str = "",
    operator: str = Depends(_get_current_user),
):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.submit_for_review(page_id, operator, comment)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message, "from_status": result.from_status, "to_status": result.to_status}


@wiki_router.post("/pages/{page_id}/approve")
def approve_page(
    page_id: str,
    comment: str = "",
    operator: str = Depends(_get_current_user),
):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.approve(page_id, operator, comment)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message}


@wiki_router.post("/pages/{page_id}/reject")
def reject_page(
    page_id: str,
    comment: str = "",
    operator: str = Depends(_get_current_user),
):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.reject(page_id, operator, comment)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message}


@wiki_router.post("/pages/{page_id}/deprecate")
def deprecate_page(
    page_id: str,
    comment: str = "",
    operator: str = Depends(_get_current_user),
):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.deprecate(page_id, operator, comment)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message}


@wiki_router.get("/pages/{page_id}/workflow")
def get_workflow_history(page_id: str, container: AppContainer = Depends(get_container)):
    from src.services.wiki_workflow import WikiWorkflow
    return {"history": WikiWorkflow.get_history(page_id)}


@wiki_router.get("/pages/{page_id}/versions")
def list_wiki_versions(page_id: str, container: AppContainer = Depends(get_container)):
    return {"versions": container.db.list_wiki_versions(page_id)}


@wiki_router.post("/pages/{page_id}/versions/{version}/restore")
def restore_wiki_version(page_id: str, version: int, container: AppContainer = Depends(get_container)):
    from src.services.wiki_workflow import WikiWorkflow
    result = WikiWorkflow.restore_version(page_id, version)
    if not result.success:
        raise HTTPException(400, result.message)
    return {"message": result.message}
