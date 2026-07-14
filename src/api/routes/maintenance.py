"""维护中心路由 — 版本冲突检测 + Wiki 维护控制面（Phase 5）"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer

maintenance_router = APIRouter(
    prefix="/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(_check_auth)],
)


def _get_wiki_maintenance(container: AppContainer = Depends(get_container)):
    return container.wiki_maintenance_service


class CreateSessionReq(BaseModel):
    rescan_ignored: bool = False


class DeletePairReq(BaseModel):
    operator: str = "user"


def _get_service(container: AppContainer = Depends(get_container)):
    from src.services.version_conflict import VersionConflictService
    return VersionConflictService(operation_log=container.operation_log)


# ── 会话管理 ──

@maintenance_router.post("/version-conflict/sessions")
def create_session(req: CreateSessionReq, svc=Depends(_get_service)):
    """创建扫描会话。返回 session_id（异步执行）。"""
    session_id = svc.start_scan_session(rescan_ignored=req.rescan_ignored)
    return {"session_id": session_id, "status": "scanning"}


@maintenance_router.get("/version-conflict/sessions")
def list_sessions(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    svc=Depends(_get_service),
):
    """列出扫描会话。"""
    sessions = svc.list_sessions(status=status, limit=limit, offset=offset)
    return {"sessions": sessions}


@maintenance_router.get("/version-conflict/sessions/{session_id}")
def get_session(session_id: str, svc=Depends(_get_service)):
    """会话详情。"""
    status = svc.get_session_status(session_id)
    # session 不存在时 service 返回 {"error": "session not found", ...}
    if status.get("error") == "session not found":
        raise HTTPException(404, f"会话不存在: {session_id}")
    return status


# ── 候选对查询 ──

@maintenance_router.get("/version-conflict/sessions/{session_id}/pairs")
def list_pairs(
    session_id: str,
    status: str | None = None,
    relation_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    svc=Depends(_get_service),
):
    """分页查询候选对。"""
    pairs = svc.list_pairs(
        session_id, status=status, relation_type=relation_type,
        limit=limit, offset=offset,
    )
    return {"pairs": pairs}


# ── 用户操作 ──

@maintenance_router.post("/version-conflict/sessions/{session_id}/judge")
def judge_pairs(session_id: str, limit: int = 20, svc=Depends(_get_service)):
    """触发 LLM 判断（异步 job）。"""
    return svc.judge_pending_pairs(session_id, limit=limit)


@maintenance_router.post("/version-conflict/pairs/{pair_id}/judge")
def judge_pair(pair_id: str, svc=Depends(_get_service)):
    """重新判断单个候选对。"""
    result = svc.judge_pair(pair_id, run_synchronously=True)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", {}).get("message", f"pair 不存在: {pair_id}"))
    return result


@maintenance_router.post("/version-conflict/pairs/{pair_id}/delete")
def delete_pair(pair_id: str, req: DeletePairReq, svc=Depends(_get_service)):
    """确认删除旧版本。"""
    result = svc.execute_delete(pair_id, operator=req.operator)
    if not result.get("ok"):
        code = result.get("error", {}).get("code", "INTERNAL_ERROR")
        message = result.get("error", {}).get("message", "未知错误")
        status_code = 404 if code == "NOT_FOUND" else 400
        raise HTTPException(status_code, message)
    return result


@maintenance_router.post("/version-conflict/pairs/{pair_id}/ignore")
def ignore_pair(pair_id: str, svc=Depends(_get_service)):
    """忽略该对。"""
    result = svc.ignore_pair(pair_id)
    if not result.get("ok"):
        raise HTTPException(404, f"pair 不存在: {pair_id}")
    return result


# ── 忽略列表管理 ──

@maintenance_router.get("/version-conflict/ignores")
def list_ignores(limit: int = 100, offset: int = 0, svc=Depends(_get_service)):
    """列出所有忽略记录。"""
    ignores = svc.list_ignores(limit=limit, offset=offset)
    return {"ignores": ignores}


@maintenance_router.delete("/version-conflict/ignores/{ignore_id}")
def delete_ignore(ignore_id: str, svc=Depends(_get_service)):
    """撤销忽略。"""
    result = svc.delete_ignore(ignore_id)
    if not result.get("ok"):
        raise HTTPException(404, f"忽略记录不存在: {ignore_id}")
    return result


# ── Phase 5: Wiki Maintenance Center ──


class SourceEventReq(BaseModel):
    knowledge_id: str
    event_type: str = "updated"  # created | updated | deleted
    source_path: str = ""
    source_revision: str = ""
    human_confirmed: bool = False


class ReviewResolveReq(BaseModel):
    action: str  # confirm | reject | correct | needs_review | defer
    operator: str = "user"
    note: str = ""
    correction: str | None = None
    human_confirmed: bool = False


class DraftProposeReq(BaseModel):
    claim_id: str = ""
    proposed: dict | None = None
    evidence: list[dict] | None = None
    reason_codes: list[str] | None = None


class R4EvaluateReq(BaseModel):
    job_type: str = "publish"
    human_confirmed: bool = False


@maintenance_router.get("/health")
def maintenance_health(svc=Depends(_get_wiki_maintenance)):
    """维护中心健康快照。失败不影响 Raw Search。"""
    try:
        return svc.health_snapshot()
    except Exception as e:
        return {
            "captured_at": None,
            "errors": [str(e)],
            "raw_search_unaffected": True,
        }


@maintenance_router.post("/source-events")
def handle_source_event(req: SourceEventReq, svc=Depends(_get_wiki_maintenance)):
    """来源变更 → Impact Plan → Policy → 保护性执行或审阅。"""
    return svc.handle_source_event(
        req.knowledge_id,
        req.event_type,
        source_path=req.source_path,
        source_revision=req.source_revision,
        human_confirmed=req.human_confirmed,
    )


@maintenance_router.get("/jobs")
def list_maintenance_jobs(
    status: str | None = None,
    limit: int = 50,
    svc=Depends(_get_wiki_maintenance),
):
    return {"jobs": svc.list_jobs(status=status, limit=limit)}


@maintenance_router.get("/jobs/{job_id}")
def get_maintenance_job(job_id: str, svc=Depends(_get_wiki_maintenance)):
    job = svc.get_job(job_id)
    if not job:
        raise HTTPException(404, f"job 不存在: {job_id}")
    return job


@maintenance_router.get("/dead-letters")
def list_maintenance_dead_letters(limit: int = 50, svc=Depends(_get_wiki_maintenance)):
    return {"dead_letters": svc.list_dead_letters(limit=limit)}


@maintenance_router.get("/health/history")
def maintenance_health_history(limit: int = 50, svc=Depends(_get_wiki_maintenance)):
    return {"snapshots": svc.health_history(limit=limit)}


@maintenance_router.post("/jobs/{job_id}/retry")
def retry_maintenance_job(job_id: str, svc=Depends(_get_wiki_maintenance)):
    result = svc.retry_job(job_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "retry failed"))
    return result


@maintenance_router.post("/jobs/{job_id}/cancel")
def cancel_maintenance_job(job_id: str, svc=Depends(_get_wiki_maintenance)):
    result = svc.cancel_job(job_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "cancel failed"))
    return result


@maintenance_router.get("/reviews")
def list_reviews(
    status: str | None = "open",
    review_type: str | None = None,
    limit: int = 50,
    svc=Depends(_get_wiki_maintenance),
):
    return {
        "reviews": svc.list_reviews(
            status=status, review_type=review_type, limit=limit,
        ),
    }


@maintenance_router.get("/reviews/{review_id}")
def get_review(review_id: str, svc=Depends(_get_wiki_maintenance)):
    review = svc.get_review(review_id)
    if not review:
        raise HTTPException(404, f"review 不存在: {review_id}")
    return review


@maintenance_router.post("/reviews/{review_id}/resolve")
def resolve_review(
    review_id: str,
    req: ReviewResolveReq,
    svc=Depends(_get_wiki_maintenance),
):
    result = svc.resolve_review(
        review_id,
        req.action,
        operator=req.operator,
        note=req.note,
        correction=req.correction,
        human_confirmed=req.human_confirmed,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "resolve failed"))
    return result


@maintenance_router.post("/drafts")
def propose_draft(req: DraftProposeReq, svc=Depends(_get_wiki_maintenance)):
    """R3: 生成 Draft/建议进入审阅，不发布。"""
    result = svc.propose_draft(
        claim_id=req.claim_id,
        proposed=req.proposed,
        evidence=req.evidence,
        reason_codes=req.reason_codes,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("decision", result))
    return result


@maintenance_router.post("/policy/evaluate-r4")
def evaluate_r4(req: R4EvaluateReq, svc=Depends(_get_wiki_maintenance)):
    """R4 决策预检：无人工确认不得执行。"""
    return svc.evaluate_r4(
        req.job_type, human_confirmed=req.human_confirmed,
    )
