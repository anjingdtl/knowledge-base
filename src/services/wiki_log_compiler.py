"""``wiki/log.md`` 生成器:追加 ingest/query/lint 时间线。

幂等:同 ``(type,target,timestamp)`` 不重复(以 hash 注释标记)。
``rebuild`` 从事件列表全量重建(去重 + 按 timestamp 排序)。
时间戳由调用方传入(可复现)。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from src.utils.config import Config


class WikiLogCompiler:
    def append(self, event: dict) -> dict:
        """追加单条事件;同事件已存在则跳过。"""
        wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))
        wiki_dir.mkdir(parents=True, exist_ok=True)
        log_path = wiki_dir / "log.md"
        h = self._event_hash(event)
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        if h in existing:
            return {"status": "duplicate", "path": str(log_path)}
        line = self._format(event, h)
        with log_path.open("a", encoding="utf-8") as f:
            if not existing:
                f.write("# Wiki Log\n\n")
            f.write(line + "\n")
        return {"status": "appended", "path": str(log_path)}

    def rebuild(self, events: list[dict]) -> dict:
        """从事件列表全量重建 log.md(去重 + 按 timestamp 排序)。"""
        wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))
        wiki_dir.mkdir(parents=True, exist_ok=True)
        log_path = wiki_dir / "log.md"
        seen: set[str] = set()
        unique: list[dict] = []
        for ev in events:
            h = self._event_hash(ev)
            if h in seen:
                continue
            seen.add(h)
            unique.append(ev)
        unique.sort(key=lambda e: e.get("timestamp", ""))
        lines = ["# Wiki Log", ""]
        for ev in unique:
            lines.append(self._format(ev, self._event_hash(ev)))
        lines.append("")
        log_path.write_text("\n".join(lines), encoding="utf-8")
        return {"status": "rebuilt", "path": str(log_path), "entries": len(unique)}

    @staticmethod
    def _format(event: dict, h: str) -> str:
        etype = event.get("type", "event")
        target = event.get("target", "")
        ts = event.get("timestamp", "")
        detail = event.get("detail", "")
        return f"- [{ts}] **{etype}**: {target} — {detail} <!-- {h} -->"

    @staticmethod
    def _event_hash(event: dict) -> str:
        key = f"{event.get('type')}|{event.get('target')}|{event.get('timestamp')}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
