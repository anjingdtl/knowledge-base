"""异步任务处理器注册"""
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

from src.services.async_worker import TaskRegistry
from src.services.indexer import reindex_all
from src.services.wiki_compiler import WikiCompiler
from src.services.wiki_lint import WikiLint

logger = logging.getLogger(__name__)


def _reindex_all_handler(job_id: str, params: dict) -> dict:
    """全量重建索引任务

    params:
        restart: bool = True — 断点续传；缺向量条目仍会重跑（indexer 内判断）
        force: bool = False — True 时等价 restart=False，忽略断点全量重建
    """
    from src.services.async_task import AsyncTaskService

    def progress_callback(current: int, total: int, message: str = ""):
        pct = int(current / total * 100) if total > 0 else 0
        AsyncTaskService.update_progress(job_id, pct, message)

    force = bool(params.get("force", False))
    restart = False if force else bool(params.get("restart", True))
    try:
        result = reindex_all(progress_callback=progress_callback, restart=restart)
        AsyncTaskService.update_progress(job_id, 100, "Index rebuild completed")
        return {
            "status": "success",
            "total": result.get("total", 0),
            "success": result.get("success", 0),
            "skipped": result.get("skipped", 0),
            "failed": result.get("failed", 0),
            "restart": restart,
        }
    except Exception as e:
        logger.error(f"Reindex job {job_id} failed: {e}")
        raise


def _wiki_compile_handler(job_id: str, params: dict) -> dict:
    """Wiki 编译任务"""
    from src.services.async_task import AsyncTaskService

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


# ---- Phase 5: 文件/URL 异步导入 ----


def _file_ingest_handler(job_id: str, params: dict) -> dict:
    """异步文件导入 handler

    处理流程：
    1. 验证文件路径
    2. 解析文件（parse_file）
    3. 逐条 ParsedFile 创建知识条目 + 向量索引
    4. 每条完成后上报进度 + cancel 检查
    5. 返回结构化结果（created_items / skipped_items / failed_items / counts）
    """
    from src.services.async_task import AsyncTaskService
    from src.services.async_worker import TaskRegistry
    from src.services.file_parser import parse_file

    file_path: str = params.get("file_path", "")
    tags: list[str] = params.get("tags", [])

    AsyncTaskService.update_progress(job_id, 5, f"验证文件: {os.path.basename(file_path)}")

    # 路径验证 — 复用 mcp_server 中的 _validate_file_path
    try:
        from src.mcp.tools.ingest import _validate_file_path
        validated_path = _validate_file_path(file_path)
    except (FileNotFoundError, PermissionError) as exc:
        raise RuntimeError(str(exc)) from exc

    # 解析文件
    AsyncTaskService.update_progress(job_id, 10, f"解析文件: {os.path.basename(validated_path)}")
    parsed_list = parse_file(validated_path)
    total_items = len(parsed_list)

    # 文件元信息
    file_created_at = ""
    file_modified_at = ""
    file_size = 0
    try:
        file_created_at = datetime.fromtimestamp(
            os.path.getctime(validated_path), tz=timezone.utc
        ).isoformat()
    except OSError:
        pass
    try:
        file_modified_at = datetime.fromtimestamp(
            os.path.getmtime(validated_path), tz=timezone.utc
        ).isoformat()
    except OSError:
        pass
    try:
        file_size = os.path.getsize(validated_path)
    except OSError:
        pass

    # 获取容器服务
    from src.core.container import AppContainer
    container: AppContainer = _get_container_for_handler()
    db = container.db

    # 尝试导入 wiki 编译
    compile_wiki: Callable[[str], Any] | None
    try:
        from src.services.wiki_compiler import try_wiki_compile as compile_wiki
    except ImportError:
        compile_wiki = None

    created_items: list[dict] = []
    skipped_items: list[dict] = []
    failed_items: list[dict] = []
    total_blocks = 0
    sheet_count = 0
    page_count = 0

    for i, parsed in enumerate(parsed_list):
        # Cancel 检查
        if TaskRegistry.is_cancelled(job_id):
            AsyncTaskService.update_progress(
                job_id,
                int((i + 1) / total_items * 100),
                f"已取消（处理到第 {i+1}/{total_items} 项）",
            )
            raise RuntimeError(f"Job {job_id} cancelled by user")

        # 统计类型
        ft = parsed.file_type.lower()
        if ft in ("xlsx", "xls", "excel"):
            sheet_count += 1
        elif ft == "pdf":
            page_count += 1

        pct = 10 + int((i + 1) / total_items * 85)
        AsyncTaskService.update_progress(
            job_id, pct, f"导入 {i+1}/{total_items}: {parsed.title[:50]}"
        )

        try:
            # 哈希去重
            content_hash = hashlib.sha256(parsed.content.encode("utf-8", errors="surrogatepass")).hexdigest()
            existing = db.get_knowledge_by_hash(content_hash)
            if existing:
                skipped_items.append({
                    "id": existing["id"],
                    "title": existing["title"],
                    "file_type": existing.get("file_type", ""),
                    "reason": "内容已存在",
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
                content_hash=content_hash,
            )

            # 统计 block 数
            try:
                rows = db.get_conn().execute(
                    "SELECT COUNT(*) as cnt FROM blocks WHERE page_id = ?", (item_id,)
                ).fetchone()
                total_blocks += rows["cnt"]
            except Exception:
                pass

            # Wiki 编译
            if compile_wiki is not None:
                try:
                    compile_wiki(item_id)
                except Exception:
                    pass

            # 操作日志
            try:
                container.operation_log.log(
                    operation="ingest",
                    target_type="knowledge",
                    target_id=item_id,
                    source="job",
                    after={"title": parsed.title, "file_type": parsed.file_type},
                    metadata={"source": "file", "path": validated_path, "job_id": job_id},
                )
            except Exception:
                pass

            item = db.get_knowledge(item_id) or {"title": parsed.title}
            created_items.append({
                "id": item_id,
                "title": item.get("title", parsed.title),
                "file_type": item.get("file_type", parsed.file_type),
            })
        except Exception as exc:
            logger.warning("file_ingest: failed to import %s: %s", parsed.title, exc)
            failed_items.append({
                "title": parsed.title,
                "file_type": parsed.file_type,
                "error": str(exc),
            })

    AsyncTaskService.update_progress(job_id, 100, f"导入完成: {len(created_items)} 成功")

    return {
        "created_items": created_items,
        "skipped_items": skipped_items,
        "failed_items": failed_items,
        "sheet_count": sheet_count,
        "page_count": page_count,
        "block_count": total_blocks,
        "total_items": total_items,
        "file_path": validated_path,
        "file_size": file_size,
    }


def _url_ingest_handler(job_id: str, params: dict) -> dict:
    """异步 URL 导入 handler"""
    from src.services.async_task import AsyncTaskService
    from src.services.async_worker import TaskRegistry
    from src.services.file_parser import parse_url

    url: str = params.get("url", "")
    tags: list[str] = params.get("tags", [])

    AsyncTaskService.update_progress(job_id, 10, f"抓取网页: {url[:80]}")

    parsed = parse_url(url)

    # Cancel 检查
    if TaskRegistry.is_cancelled(job_id):
        raise RuntimeError(f"Job {job_id} cancelled by user")

    AsyncTaskService.update_progress(job_id, 50, f"解析完成，创建知识条目: {parsed.title[:50]}")

    container = _get_container_for_handler()
    db = container.db

    # 哈希去重
    content_hash = hashlib.sha256(parsed.content.encode("utf-8", errors="surrogatepass")).hexdigest()
    existing = db.get_knowledge_by_hash(content_hash)
    if existing:
        AsyncTaskService.update_progress(job_id, 100, "网页内容已存在，跳过")
        return {
            "created_items": [],
            "skipped_items": [{
                "id": existing["id"],
                "title": existing["title"],
                "file_type": existing.get("file_type", ""),
                "reason": "网页内容已存在",
            }],
            "failed_items": [],
            "sheet_count": 0,
            "page_count": 0,
            "block_count": 0,
            "total_items": 1,
            "url": url,
        }

    blocks = parsed.structured if parsed.structured else parsed.content
    item_id = container.file_graph_service.create_page(
        parsed.title,
        blocks,
        tags=tags,
        metadata={
            "source_type": "web",
            "source_path": parsed.source_path,
            "file_type": parsed.file_type,
        },
    )

    # Wiki 编译
    try:
        from src.services.wiki_compiler import try_wiki_compile
        try_wiki_compile(item_id)
    except Exception:
        pass

    # 操作日志
    try:
        container.operation_log.log(
            operation="ingest",
            target_type="knowledge",
            target_id=item_id,
            source="job",
            after={"title": parsed.title, "file_type": parsed.file_type},
            metadata={"source": "url", "url": url, "job_id": job_id},
        )
    except Exception:
        pass

    # 统计 block 数
    block_count = 0
    try:
        rows = db.get_conn().execute(
            "SELECT COUNT(*) as cnt FROM blocks WHERE page_id = ?", (item_id,)
        ).fetchone()
        block_count = rows["cnt"]
    except Exception:
        pass

    item = db.get_knowledge(item_id) or {"title": parsed.title}
    AsyncTaskService.update_progress(job_id, 100, "导入完成")

    return {
        "created_items": [{
            "id": item_id,
            "title": item.get("title", parsed.title),
            "file_type": item.get("file_type", parsed.file_type),
        }],
        "skipped_items": [],
        "failed_items": [],
        "sheet_count": 0,
        "page_count": 0,
        "block_count": block_count,
        "total_items": 1,
        "url": url,
    }


def _get_container_for_handler():
    """为 handler 获取 AppContainer 实例

    优先复用全局 _container（来自 MCP lifespan），否则延迟创建。
    """
    try:
        from src.mcp_server import _get_container
        return _get_container()
    except Exception:
        from src.core.container import create_container
        return create_container()


def _estimate_file_complexity(file_path: str) -> dict:
    """估算文件复杂度，用于判定是否需要异步导入

    Returns:
        dict 含 size_bytes / sheet_count / page_count / paragraph_count / needs_async
    """
    size_bytes = 0
    sheet_count = 0
    page_count = 0
    paragraph_count = 0

    try:
        size_bytes = os.path.getsize(file_path)
    except OSError:
        pass

    from src.utils.config import Config
    size_threshold = int(Config.get("ingest.size_threshold_bytes", 5_000_000))
    ext = os.path.splitext(file_path)[1].lower()

    # Excel: 读取 sheet 数（快速探测，不解析内容）
    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True)
            sheet_count = len(wb.sheetnames)
            wb.close()
        except Exception:
            pass
        sheet_threshold = int(Config.get("ingest.excel.sheet_count_threshold", 5))
        if sheet_count > sheet_threshold:
            return {
                "size_bytes": size_bytes,
                "sheet_count": sheet_count,
                "page_count": 0,
                "paragraph_count": 0,
                "needs_async": True,
                "reason": f"Excel sheet 数 ({sheet_count}) 超过阈值 ({sheet_threshold})",
            }

    # PDF: 读取页数
    elif ext == ".pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            page_count = doc.page_count
            doc.close()
        except Exception:
            pass
        page_threshold = int(Config.get("ingest.pdf.page_count_threshold", 50))
        if page_count > page_threshold:
            return {
                "size_bytes": size_bytes,
                "sheet_count": 0,
                "page_count": page_count,
                "paragraph_count": 0,
                "needs_async": True,
                "reason": f"PDF 页数 ({page_count}) 超过阈值 ({page_threshold})",
            }

    # DOCX: 读取段落数
    elif ext in (".docx", ".doc"):
        try:
            from docx import Document
            doc = Document(file_path)
            paragraph_count = len(doc.paragraphs)
        except Exception:
            pass
        para_threshold = int(Config.get("ingest.docx.paragraph_count_threshold", 200))
        if paragraph_count > para_threshold:
            return {
                "size_bytes": size_bytes,
                "sheet_count": 0,
                "page_count": 0,
                "paragraph_count": paragraph_count,
                "needs_async": True,
                "reason": f"DOCX 段落数 ({paragraph_count}) 超过阈值 ({para_threshold})",
            }

    # 通用大小阈值
    if size_bytes > size_threshold:
        return {
            "size_bytes": size_bytes,
            "sheet_count": sheet_count,
            "page_count": page_count,
            "paragraph_count": paragraph_count,
            "needs_async": True,
            "reason": f"文件大小 ({size_bytes} bytes) 超过阈值 ({size_threshold} bytes)",
        }

    return {
        "size_bytes": size_bytes,
        "sheet_count": sheet_count,
        "page_count": page_count,
        "paragraph_count": paragraph_count,
        "needs_async": False,
        "reason": "",
    }


# ---- M3: 路径扫描异步任务 ----


def _path_scan_handler(job_id: str, params: dict) -> dict:
    """路径扫描异步 handler — 计算增量差异并逐项应用"""
    from pathlib import Path

    from src.models.indexing import ManifestDiff
    from src.services.async_task import AsyncTaskService
    from src.services.async_worker import TaskRegistry

    container = _get_container_for_handler()
    indexer = container.path_indexer

    root_value = params.get("root")
    file_paths: list[str] = params.get("file_paths", [])
    if not root_value and file_paths:
        common = Path(os.path.commonpath(file_paths))
        root_value = str(common if common.is_dir() else common.parent)
    if not root_value:
        raise RuntimeError("path_scan job missing root")

    root = Path(root_value)
    recursive = bool(params.get("recursive", True))
    force = bool(params.get("force", False))
    manifest = indexer.scan_manifest(root, recursive=recursive)
    diff = indexer.compute_diff(manifest, root, force=force)

    operations = (
        [("created", fp) for fp in diff.created]
        + [("modified", fp) for fp in diff.modified]
        + [("deleted", path) for path in diff.deleted]
    )
    total = len(operations)
    created = 0
    updated = 0
    deleted = 0
    skipped = len(diff.unchanged)
    failed_items: list[dict] = []

    for i, (change_type, value) in enumerate(operations):
        if TaskRegistry.is_cancelled(job_id):
            AsyncTaskService.update_progress(
                job_id,
                int(i / max(total, 1) * 100),
                f"已取消（处理到第 {i}/{total} 项）",
            )
            raise RuntimeError(f"Job {job_id} cancelled by user")

        display_path = value.path if hasattr(value, "path") else value
        pct = int((i + 1) / max(total, 1) * 100)
        AsyncTaskService.update_progress(
            job_id,
            pct,
            f"处理 {i+1}/{total}: {os.path.basename(str(display_path))}",
        )
        try:
            if change_type == "created":
                partial = ManifestDiff(created=[value])
            elif change_type == "modified":
                partial = ManifestDiff(modified=[value])
            else:
                partial = ManifestDiff(deleted=[value])
            result = indexer.apply_diff(partial)
            created += result.created
            updated += result.updated
            deleted += result.deleted
            skipped += result.skipped
            failed_items.extend(result.failed)
        except Exception as e:
            logger.warning("path_scan: failed to process %s: %s", display_path, e)
            failed_items.append({"path": str(display_path), "error": str(e)})

    AsyncTaskService.update_progress(
        job_id,
        100,
        f"扫描完成: {created} 新建, {updated} 更新, {deleted} 删除, {skipped} 跳过",
    )

    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "skipped": skipped,
        "failed": failed_items,
        "total": len(manifest) + len(diff.deleted),
    }


# 注册所有任务处理器
def _version_conflict_scan_handler(job_id: str, params: dict) -> dict:
    """版本冲突扫描任务"""
    from src.services.async_task import AsyncTaskService
    from src.services.version_conflict import VersionConflictService

    session_id = params.get("session_id", "")
    rescan_ignored = params.get("rescan_ignored", False)
    AsyncTaskService.update_progress(job_id, 10, f"Scanning session {session_id}...")

    svc = VersionConflictService()
    try:
        svc._run_scan(session_id, rescan_ignored=rescan_ignored)
        AsyncTaskService.update_progress(job_id, 100, "Scan completed")
        return {"status": "success", "session_id": session_id}
    except Exception as e:
        logger.error(f"Version conflict scan {job_id} failed: {e}")
        raise


def _version_conflict_judge_handler(job_id: str, params: dict) -> dict:
    """版本冲突 LLM 判断任务"""
    from src.services.async_task import AsyncTaskService
    from src.services.version_conflict import VersionConflictService

    session_id = params.get("session_id", "")
    limit = params.get("limit", 20)
    AsyncTaskService.update_progress(job_id, 10, f"Judging session {session_id}...")

    svc = VersionConflictService()
    try:
        result = svc.judge_pending_pairs(session_id, limit=limit, run_synchronously=True)
        AsyncTaskService.update_progress(job_id, 100, "Judge completed")
        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"Version conflict judge {job_id} failed: {e}")
        raise


def register_all_tasks():
    """注册所有任务处理器（在应用启动时调用）"""
    TaskRegistry.register("reindex_all", _reindex_all_handler)
    TaskRegistry.register("wiki_compile", _wiki_compile_handler)
    TaskRegistry.register("wiki_lint", _wiki_lint_handler)
    TaskRegistry.register("wiki_site_generate", _wiki_site_generate_handler)
    TaskRegistry.register("file_ingest", _file_ingest_handler)
    TaskRegistry.register("url_ingest", _url_ingest_handler)
    TaskRegistry.register("path_scan", _path_scan_handler)
    TaskRegistry.register("version_conflict_scan", _version_conflict_scan_handler)
    TaskRegistry.register("version_conflict_judge", _version_conflict_judge_handler)
    logger.info("All async task handlers registered")


# 模块加载时自动注册
register_all_tasks()
