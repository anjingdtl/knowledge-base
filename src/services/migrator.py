"""MigrationService — 把 legacy 项目迁移到 wiki-first。

流程(spec §10):
1. plan() / --dry-run:扫描 data/ knowledge,输出计划(导出哪些源、重编译哪些),不写盘
2. apply() / --apply:备份 data/ → 按 source_path 导出源到 raw/ → 触发 wiki 重编译 → 切 mode

不删除 data/,双轨过渡;失败可从备份回滚。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.services.db import Database
from src.utils.config import Config

logger = logging.getLogger(__name__)


class MigrationService:
    def __init__(self, project_dir: Path | None = None):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()

    def _ensure_db(self) -> None:
        """确保 Database 全局实例已初始化。

        CLI migrate 不走 AppContainer，Database._instance 默认为 None，
        导致 _DatabaseMeta 无法把类级调用 Database.list_knowledge() 委托到实例
        （报 missing 'self'）。此处显式连库，与 container 路径对齐。
        """
        if Database._instance is None:
            data_dir = Config.get("storage.data_dir", "data")
            db_name = Config.get("storage.db_name", "kb.db")
            Database.connect(str(Path(data_dir) / db_name))

    def plan(self) -> dict:
        """扫描 knowledge,输出迁移计划(不写盘)。"""
        self._ensure_db()
        items = Database.list_knowledge(limit=10000)
        actions = []
        for it in items:
            if it.get("source_type") != "file":
                continue
            sp = it.get("source_path", "")
            exists = bool(sp) and Path(sp).exists()
            actions.append({
                "knowledge_id": it["id"],
                "title": it.get("title", ""),
                "source_path": sp,
                "source_exists": exists,
                "action": "export" if exists else "skip_missing",
            })
        return {
            "knowledge_count": len(items),
            "exportable": sum(1 for a in actions if a["action"] == "export"),
            "actions": actions,
        }

    def apply(self, backup: bool = True) -> dict:
        """备份 data/ + 导出源到 raw/ + 触发重编译。

        wiki 重编译依赖 active container（try_knowledge_workflow_compile 从中取
        knowledge_workflow 服务）；CLI 路径由 _handle_migrate 负责调用 create_container()，
        本方法不自行创建容器，以保持可测试性。
        """
        self._ensure_db()
        raw_dir = Path(Config.get("knowledge_workflow.raw_dir", "raw"))
        raw_dir.mkdir(parents=True, exist_ok=True)

        data_dir_str = Config.get("storage.data_dir", "data")
        data_dir = Path(data_dir_str)
        backup_created = False
        if backup and data_dir.exists():
            backup_path = data_dir.parent / f"{data_dir.name}.backup"
            # 先写临时备份,成功后才替换旧备份——避免 ``rmtree(旧) + copytree`` 之间
            # copytree 中途失败导致旧备份已删、新备份半写的数据丢失(不可逆)。
            tmp_backup = data_dir.parent / f"{data_dir.name}.backup.tmp"
            try:
                if tmp_backup.exists():
                    shutil.rmtree(tmp_backup)
                shutil.copytree(data_dir, tmp_backup)
            except Exception:
                if tmp_backup.exists():
                    shutil.rmtree(tmp_backup, ignore_errors=True)
                raise
            if backup_path.exists():
                shutil.rmtree(backup_path)
            shutil.move(str(tmp_backup), str(backup_path))
            backup_created = True
            logger.info("data/ backed up to %s", backup_path)

        exported = 0
        skipped_missing = 0
        items = Database.list_knowledge(limit=10000)
        for it in items:
            if it.get("source_type") != "file":
                continue
            sp = it.get("source_path", "")
            if not sp or not Path(sp).exists():
                skipped_missing += 1
                continue
            src = Path(sp)
            dest = raw_dir / src.name
            # 同名且内容不同 → 加 knowledge_id 短缀
            if dest.exists() and dest.read_bytes() != src.read_bytes():
                dest = raw_dir / f"{src.stem}-{it['id'][:8]}{src.suffix}"
            shutil.copy2(src, dest)
            exported += 1

        # 触发 wiki 重编译(每个 file knowledge)
        recompiled = 0
        try:
            from src.services.knowledge_workflow import try_knowledge_workflow_compile
            for it in items:
                if it.get("source_type") == "file":
                    try_knowledge_workflow_compile(
                        it["id"], ingested_at=it.get("created_at", "")
                    )
                    recompiled += 1
        except Exception as e:
            logger.warning("recompile during migrate failed: %s", e)

        return {
            "exported": exported,
            "skipped_missing": skipped_missing,
            "recompiled": recompiled,
            "backup_created": backup_created,
        }
