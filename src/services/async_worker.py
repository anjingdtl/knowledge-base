"""异步任务后台 Worker 与任务注册表"""
import logging
import threading
import time
from typing import Callable, Optional

from src.services.async_task import AsyncJob, JobStatus, AsyncTaskService

# 取消标记的过期时间（秒）
_CANCELLED_JOBS_TTL = 3600  # 1 小时

logger = logging.getLogger(__name__)


class TaskRegistry:
    """任务类型注册表"""
    _handlers: dict[str, Callable] = {}
    # {job_id: cancel_timestamp} — 带 TTL 的取消标记
    _cancelled_jobs: dict[str, float] = {}
    _last_cleanup: float = 0.0

    @classmethod
    def register(cls, job_type: str, handler: Callable):
        """注册任务处理器"""
        cls._handlers[job_type] = handler
        logger.info(f"Registered task handler: {job_type}")

    @classmethod
    def get_handler(cls, job_type: str) -> Callable | None:
        """获取任务处理器"""
        return cls._handlers.get(job_type)

    @classmethod
    def _maybe_cleanup(cls):
        """定期清理过期的取消标记"""
        now = time.monotonic()
        # 每 5 分钟最多清理一次
        if now - cls._last_cleanup < 300:
            return
        cls._last_cleanup = now
        expired = [jid for jid, ts in cls._cancelled_jobs.items()
                   if now - ts > _CANCELLED_JOBS_TTL]
        for jid in expired:
            del cls._cancelled_jobs[jid]
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired cancel markers")

    @classmethod
    def cancel_job(cls, job_id: str):
        """标记任务为取消"""
        cls._cancelled_jobs[job_id] = time.monotonic()

    @classmethod
    def is_cancelled(cls, job_id: str) -> bool:
        """检查任务是否被取消（未过期）"""
        ts = cls._cancelled_jobs.get(job_id)
        if ts is None:
            return False
        if time.monotonic() - ts > _CANCELLED_JOBS_TTL:
            del cls._cancelled_jobs[job_id]
            return False
        return True

    @classmethod
    def clear_cancelled(cls, job_id: str):
        """清除取消标记"""
        cls._cancelled_jobs.pop(job_id, None)


class AsyncWorker:
    """后台任务执行器"""

    _instance: Optional["AsyncWorker"] = None
    _lock = threading.Lock()

    def __init__(self, poll_interval: float = 1.0, max_workers: int = 2):
        self._poll_interval = poll_interval
        self._max_workers = max_workers
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._workers: list[threading.Thread] = []

    @classmethod
    def get_instance(cls, poll_interval: float = 1.0, max_workers: int = 2) -> "AsyncWorker":
        """单例获取"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(poll_interval, max_workers)
            return cls._instance

    def start(self):
        """启动 Worker"""
        if self._running:
            return
        self._running = True
        # 启动主调度线程
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="AsyncWorker-Main")
        self._thread.start()
        logger.info("AsyncWorker started")

    def stop(self):
        """停止 Worker"""
        self._running = False
        for w in self._workers:
            w.join(timeout=5)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("AsyncWorker stopped")

    def _run_loop(self):
        """主循环：认领并分发任务到工作线程池"""
        from concurrent.futures import ThreadPoolExecutor
        active = set()
        lock = threading.Lock()

        def _run_and_track(fut):
            """线程完成回调：从活跃集合中移除"""
            with lock:
                active.discard(fut)

        with ThreadPoolExecutor(max_workers=self._max_workers,
                                thread_name_prefix="AsyncWorker") as executor:
            while self._running:
                try:
                    # 清理已完成的 future
                    with lock:
                        done = {f for f in active if f.done()}
                        active -= done

                    # 只在有空闲线程时才认领新任务
                    with lock:
                        slots = self._max_workers - len(active)

                    if slots > 0:
                        job = AsyncTaskService.claim_job()
                        if job:
                            fut = executor.submit(self._execute_job, job)
                            fut.add_done_callback(_run_and_track)
                            with lock:
                                active.add(fut)
                            continue  # 立即尝试认领下一个

                    time.sleep(self._poll_interval)
                    TaskRegistry._maybe_cleanup()
                except Exception as e:
                    logger.error(f"Worker loop error: {e}", exc_info=True)
                    time.sleep(self._poll_interval)

    def _execute_job(self, job: AsyncJob):
        """执行单个任务"""
        handler = TaskRegistry.get_handler(job.job_type)
        if not handler:
            logger.error(f"No handler registered for job type: {job.job_type}")
            AsyncTaskService.update_status(job.id, JobStatus.FAILED,
                                           error=f"No handler for {job.job_type}")
            return

        logger.info(f"Executing job: {job.id} ({job.job_type})")
        TaskRegistry.clear_cancelled(job.id)

        try:
            result = handler(job.id, job.params)
            AsyncTaskService.update_status(job.id, JobStatus.COMPLETED, result=result)
            logger.info(f"Job completed: {job.id}")
        except Exception as e:
            logger.error(f"Job {job.id} failed: {e}", exc_info=True)
            if job.retry_count < job.max_retries:
                # 原子操作：同时更新状态为 pending 并递增重试计数
                from src.services.db import Database
                Database.get_conn().execute(
                    "UPDATE async_jobs SET status = 'pending', retry_count = retry_count + 1, "
                    "started_at = NULL WHERE id = ?",
                    (job.id,),
                )
                Database.get_conn().commit()
                logger.info(f"Job {job.id} retrying (attempt {job.retry_count + 1}/{job.max_retries})")
            else:
                AsyncTaskService.update_status(job.id, JobStatus.FAILED, error=str(e))


# 全局 Worker 实例访问函数
def get_worker() -> AsyncWorker:
    """获取���局 Worker 实例"""
    return AsyncWorker.get_instance()


def start_worker(poll_interval: float = 1.0, max_workers: int = 2):
    """启动全局 Worker"""
    worker = get_worker(poll_interval, max_workers)
    worker.start()


def stop_worker():
    """停止全局 Worker"""
    worker = get_worker()
    worker.stop()