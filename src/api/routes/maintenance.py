"""维护中心路由 — 版本冲突检测 + Wiki 维护控制面（Phase 5）"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.routes.auth import _check_auth

maintenance_router = APIRouter(
    prefix="/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(_check_auth)],
)


def _get_wiki_maintenance():
    from src.core.container import get_active_container
    c = get_active_container()
    if c is None:
        from src.core.container import create_container
        c = create_container()
    return c.wiki_maintenance_service


class CreateSessionReq(BaseModel):
    rescan_ignored: bool = False


class DeletePairReq(BaseModel):
    operator: str = "user"


def _get_service():
    from src.services.version_conflict import VersionConflictService
    return VersionConflictService()


# ── 会话管理 ──

@maintenance_router.post("/version-conflict/sessions")
def create_session(req: CreateSessionReq):
    """创建扫描会话。返回 session_id（异步执行）。"""
    svc = _get_service()
    session_id = svc.start_scan_session(rescan_ignored=req.rescan_ignored)
    return {"session_id": session_id, "status": "scanning"}


@maintenance_router.get("/version-conflict/sessions")
def list_sessions(status: str | None = None, limit: int = 50, offset: int = 0):
    """列出扫描会话。"""
    svc = _get_service()
    sessions = svc.list_sessions(status=status, limit=limit, offset=offset)
    return {"sessions": sessions}


@maintenance_router.get("/version-conflict/sessions/{session_id}")
def get_session(session_id: str):
    """会话详情。"""
    svc = _get_service()
    status = svc.get_session_status(session_id)
    # session 不存在时 service 返回 {"error": "session not found", ...}
    if status.get("error") == "session not found":
        raise HTTPException(404, f"会话不存在: {session_id}")
    return status


# ── 候选对查询 ──

@maintenance_router.get("/version-conflict/sessions/{session_id}/pairs")
def list_pairs(session_id: str, status: str | None = None,
               relation_type: str | None = None,
               limit: int = 50, offset: int = 0):
    """分页查询候选对。"""
    svc = _get_service()
    pairs = svc.list_pairs(
        session_id, status=status, relation_type=relation_type,
        limit=limit, offset=offset,
    )
    return {"pairs": pairs}


# ── 用户操作 ──

@maintenance_router.post("/version-conflict/sessions/{session_id}/judge")
def judge_pairs(session_id: str, limit: int = 20):
    """触发 LLM 判断（异步 job）。"""
    svc = _get_service()
    result = svc.judge_pending_pairs(session_id, limit=limit)
    return result


@maintenance_router.post("/version-conflict/pairs/{pair_id}/judge")
def judge_pair(pair_id: str):
    """重新判断单个候选对。"""
    svc = _get_service()
    result = svc.judge_pair(pair_id, run_synchronously=True)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", {}).get("message", f"pair 不存在: {pair_id}"))
    return result


@maintenance_router.post("/version-conflict/pairs/{pair_id}/delete")
def delete_pair(pair_id: str, req: DeletePairReq):
    """确认删除旧版本。"""
    svc = _get_service()
    result = svc.execute_delete(pair_id, operator=req.operator)
    if not result.get("ok"):
        code = result.get("error", {}).get("code", "INTERNAL_ERROR")
        message = result.get("error", {}).get("message", "未知错误")
        status_code = 404 if code == "NOT_FOUND" else 400
        raise HTTPException(status_code, message)
    return result


@maintenance_router.post("/version-conflict/pairs/{pair_id}/ignore")
def ignore_pair(pair_id: str):
    """忽略该对。"""
    svc = _get_service()
    result = svc.ignore_pair(pair_id)
    if not result.get("ok"):
        raise HTTPException(404, f"pair 不存在: {pair_id}")
    return result


# ── 忽略列表管理 ──

@maintenance_router.get("/version-conflict/ignores")
def list_ignores(limit: int = 100, offset: int = 0):
    """列出所有忽略记录。"""
    svc = _get_service()
    ignores = svc.list_ignores(limit=limit, offset=offset)
    return {"ignores": ignores}


@maintenance_router.delete("/version-conflict/ignores/{ignore_id}")
def delete_ignore(ignore_id: str):
    """撤销忽略。"""
    svc = _get_service()
    result = svc.delete_ignore(ignore_id)
    if not result.get("ok"):
        raise HTTPException(404, f"忽略记录不存在: {ignore_id}")
    return result


# ── Phase 5: Wiki Maintenance Center ──


class SourceEventReq(BaseModel):
    knowledge_id: str
    event_type: str = "updated"  # created | updated | deleted
    source_path: str = ""
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
def maintenance_health():
    """维护中心健康快照。失败不影响 Raw Search。"""
    try:
        return _get_wiki_maintenance().health_snapshot()
    except Exception as e:
        return {
            "captured_at": None,
            "errors": [str(e)],
            "raw_search_unaffected": True,
        }


@maintenance_router.post("/source-events")
def handle_source_event(req: SourceEventReq):
    """来源变更 → Impact Plan → Policy → 保护性执行或审阅。"""
    return _get_wiki_maintenance().handle_source_event(
        req.knowledge_id,
        req.event_type,
        source_path=req.source_path,
        human_confirmed=req.human_confirmed,
    )


@maintenance_router.get("/jobs")
def list_maintenance_jobs(status: str | None = None, limit: int = 50):
    return {"jobs": _get_wiki_maintenance().list_jobs(status=status, limit=limit)}


@maintenance_router.get("/jobs/{job_id}")
def get_maintenance_job(job_id: str):
    job = _get_wiki_maintenance().get_job(job_id)
    if not job:
        raise HTTPException(404, f"job 不存在: {job_id}")
    return job


@maintenance_router.post("/jobs/{job_id}/retry")
def retry_maintenance_job(job_id: str):
    result = _get_wiki_maintenance().retry_job(job_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "retry failed"))
    return result


@maintenance_router.post("/jobs/{job_id}/cancel")
def cancel_maintenance_job(job_id: str):
    result = _get_wiki_maintenance().cancel_job(job_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "cancel failed"))
    return result


@maintenance_router.get("/reviews")
def list_reviews(status: str | None = "open", review_type: str | None = None, limit: int = 50):
    return {
        "reviews": _get_wiki_maintenance().list_reviews(
            status=status, review_type=review_type, limit=limit,
        ),
    }


@maintenance_router.get("/reviews/{review_id}")
def get_review(review_id: str):
    review = _get_wiki_maintenance().get_review(review_id)
    if not review:
        raise HTTPException(404, f"review 不存在: {review_id}")
    return review


@maintenance_router.post("/reviews/{review_id}/resolve")
def resolve_review(review_id: str, req: ReviewResolveReq):
    result = _get_wiki_maintenance().resolve_review(
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
def propose_draft(req: DraftProposeReq):
    """R3: 生成 Draft/建议进入审阅，不发布。"""
    result = _get_wiki_maintenance().propose_draft(
        claim_id=req.claim_id,
        proposed=req.proposed,
        evidence=req.evidence,
        reason_codes=req.reason_codes,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("decision", result))
    return result


@maintenance_router.post("/policy/evaluate-r4")
def evaluate_r4(req: R4EvaluateReq):
    """R4 决策预检：无人工确认不得执行。"""
    return _get_wiki_maintenance().evaluate_r4(
        req.job_type, human_confirmed=req.human_confirmed,
    )
