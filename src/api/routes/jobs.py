from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.routes.auth import _check_auth

jobs_router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(_check_auth)])

ALLOWED_JOB_TYPES = {
    "reindex_all",
    "wiki_compile",
    "wiki_lint",
    "wiki_site_generate",
    "file_ingest",
    "url_ingest",
}


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
def list_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
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
