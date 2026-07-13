"""Safe, explicit migration for Verified Hybrid configuration files."""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.utils.knowledge_mode import resolve_knowledge_mode


@dataclass(frozen=True)
class VerifiedHybridConfigMigrationReport:
    config_path: str
    dry_run: bool
    changed: bool
    target_mode: str
    backup_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_path": self.config_path,
            "dry_run": self.dry_run,
            "changed": self.changed,
            "target_mode": self.target_mode,
            "backup_path": self.backup_path,
        }


class VerifiedHybridConfigMigrator:
    """Preserve unknown settings while normalizing only the documented keys."""

    def __init__(self, config_path: str | Path) -> None:
        self.path = Path(config_path)

    def _load(self) -> tuple[bytes, dict[str, Any]]:
        raw = self.path.read_bytes()
        data = yaml.safe_load(raw.decode("utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("配置文件格式错误（根节点必须为 mapping）")
        return raw, data

    def _proposed(self, data: dict[str, Any], target_mode: str) -> dict[str, Any]:
        if target_mode not in {"keep", "verified", "authoring", "evidence_only"}:
            raise ValueError("target_mode 必须为 keep/verified/authoring/evidence_only")
        workflow = data.setdefault("knowledge_workflow", {})
        if not isinstance(workflow, dict):
            raise ValueError("knowledge_workflow 必须为 mapping")
        raw_mode = workflow.get("mode")
        mode = resolve_knowledge_mode(raw_mode)
        if target_mode != "keep":
            mode = target_mode
        workflow["mode"] = mode

        wiki = data.setdefault("wiki", {})
        if not isinstance(wiki, dict):
            raise ValueError("wiki 必须为 mapping")
        rag = data.setdefault("rag", {})
        if not isinstance(rag, dict):
            raise ValueError("rag 必须为 mapping")
        verified = rag.setdefault("verified_knowledge", {})
        if not isinstance(verified, dict):
            raise ValueError("rag.verified_knowledge 必须为 mapping")
        mcp = data.setdefault("mcp", {})
        if not isinstance(mcp, dict):
            raise ValueError("mcp 必须为 mapping")

        is_authoring = mode == "authoring"
        is_verified = mode == "verified"
        wiki.setdefault("read_enabled", mode != "evidence_only")
        wiki.setdefault("authoring_enabled", is_authoring)
        wiki.setdefault("auto_publish", False)
        verified.setdefault("enabled", mode != "evidence_only")
        mcp.setdefault("tool_profile", "extended" if is_authoring else "core")
        mcp.setdefault("write_policy", "local_confirm" if is_authoring else "disabled")
        if is_verified:
            wiki["authoring_enabled"] = False
            wiki["auto_publish"] = False
            mcp["write_policy"] = "disabled"
        return data

    def dry_run(self, *, target_mode: str = "keep") -> VerifiedHybridConfigMigrationReport:
        raw, data = self._load()
        proposed = self._proposed(data, target_mode)
        rendered = yaml.safe_dump(proposed, allow_unicode=True, sort_keys=False).encode("utf-8")
        return VerifiedHybridConfigMigrationReport(
            config_path=str(self.path), dry_run=True, changed=rendered != raw, target_mode=target_mode,
        )

    def apply(self, *, target_mode: str = "keep") -> VerifiedHybridConfigMigrationReport:
        raw, data = self._load()
        proposed = self._proposed(data, target_mode)
        rendered = yaml.safe_dump(proposed, allow_unicode=True, sort_keys=False).encode("utf-8")
        if rendered == raw:
            return VerifiedHybridConfigMigrationReport(str(self.path), False, False, target_mode)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(f"{self.path.name}.verified-hybrid-{stamp}.bak")
        shutil.copyfile(self.path, backup)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(rendered)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return VerifiedHybridConfigMigrationReport(str(self.path), False, True, target_mode, str(backup))

    def rollback(self, backup_path: str | Path) -> VerifiedHybridConfigMigrationReport:
        backup = Path(backup_path)
        raw = backup.read_bytes()
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(raw)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return VerifiedHybridConfigMigrationReport(str(self.path), False, True, "rollback", str(backup))
