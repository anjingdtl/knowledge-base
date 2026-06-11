from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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


@wiki_router.get("/pages/{page_id}")
def get_wiki_page(page_id: str, container: AppContainer = Depends(get_container)):
    page = container.db.get_wiki_page(page_id)
    if not page:
        raise HTTPException(404, "Wiki 页面不存在")
    page["links"] = container.db.get_links_for_page(page_id)
    page["backlinks"] = container.db.get_backlinks(page_id)
    return page


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
        # dry_run: 仅扫描死链，不调用 LLM
        from src.services.wiki_compiler import _WIKI_LINK_RE
        from src.services.db import Database
        pages = Database.list_wiki_pages(limit=500)
        if not pages:
            return {"status": "empty", "scanned": 0, "dead_links": []}
        all_titles = {p["title"] for p in pages}
        dead_links = []
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
        return {
            "status": "preview",
            "scanned": min(len(pages), data.max_pages),
            "total_dead_links": len(dead_links),
            "dead_links": dead_links,
        }
    else:
        result = compiler.repair_dead_references(max_pages=data.max_pages)
        return result


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
