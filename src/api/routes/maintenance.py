"""维护中心路由 — 版本冲突检测与清理"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.routes.auth import _check_auth

maintenance_router = APIRouter(
    prefix="/maintenance",
    tags=["maintenance"],
    dependencies=[Depends(_check_auth)],
)


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
