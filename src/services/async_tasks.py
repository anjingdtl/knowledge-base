"""异步任务处理器注册"""
import logging

from src.services.async_worker import TaskRegistry
from src.services.indexer import reindex_all
from src.services.wiki_compiler import WikiCompiler
from src.services.wiki_lint import WikiLint

logger = logging.getLogger(__name__)


def _reindex_all_handler(job_id: str, params: dict) -> dict:
    """全量重建索引任务"""
    from src.services.async_task import AsyncTaskService

    def progress_callback(current: int, total: int, message: str = ""):
        pct = int(current / total * 100) if total > 0 else 0
        AsyncTaskService.update_progress(job_id, pct, message)

    try:
        result = reindex_all(progress_callback=progress_callback)
        AsyncTaskService.update_progress(job_id, 100, "Index rebuild completed")
        return {"status": "success", "total": result.get("total", 0), "success": result.get("success", 0)}
    except Exception as e:
        logger.error(f"Reindex job {job_id} failed: {e}")
        raise


def _wiki_compile_handler(job_id: str, params: dict) -> dict:
    """Wiki 编译任务"""
    from src.services.async_task import AsyncTaskService
    from src.services.db import Database

    knowledge_ids = params.get("knowledge_ids", [])
    AsyncTaskService.update_progress(job_id, 0, f"Compiling {len(knowledge_ids)} items")

    compiler = WikiCompiler()
    pages_created = 0
    pages_updated = 0

    for i, kid in enumerate(knowledge_ids):
        try:
            result = compiler.ingest(kid)
            if result:
                pages_created += len(result.get("created", []))
                pages_updated += len(result.get("updated", []))
            pct = int((i + 1) / len(knowledge_ids) * 100)
            AsyncTaskService.update_progress(job_id, pct, f"Processed {i+1}/{len(knowledge_ids)}")
        except Exception as e:
            logger.warning(f"Failed to compile knowledge {kid}: {e}")

    return {
        "pages_created": pages_created,
        "pages_updated": pages_updated,
    }


def _wiki_lint_handler(job_id: str, params: dict) -> dict:
    """Wiki 健康检查任务"""
    from src.services.async_task import AsyncTaskService

    AsyncTaskService.update_progress(job_id, 10, "Running lint checks...")
    linter = WikiLint()
    report = linter.run()
    AsyncTaskService.update_progress(job_id, 100, "Lint completed")
    return report


def _wiki_site_generate_handler(job_id: str, params: dict) -> dict:
    """Wiki 静态站点生成任务"""
    from src.services.async_task import AsyncTaskService

    output_dir = params.get("output_dir", "wiki_site")
    AsyncTaskService.update_progress(job_id, 10, "Generating static site...")

    try:
        from src.services.wiki_site import WikiSiteGenerator
        generator = WikiSiteGenerator()
        result = generator.generate_static_site(output_dir)
        AsyncTaskService.update_progress(job_id, 100, "Site generation completed")
        return {"status": "success", "output_dir": str(result)}
    except Exception as e:
        logger.error(f"Site generation failed: {e}")
        raise


# 注册所有任务处理器
def register_all_tasks():
    """注册所有任务处理器（在应用启动时调用）"""
    TaskRegistry.register("reindex_all", _reindex_all_handler)
    TaskRegistry.register("wiki_compile", _wiki_compile_handler)
    TaskRegistry.register("wiki_lint", _wiki_lint_handler)
    TaskRegistry.register("wiki_site_generate", _wiki_site_generate_handler)
    logger.info("All async task handlers registered")


# 模块加载时自动注册
register_all_tasks()