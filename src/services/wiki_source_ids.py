"""双轨 wiki frontmatter/source_ids 统一读取 helper(轻量收敛 Task 1)。

两套语义不同的解析器:
- ``resolve_source_ids(fm)``:读文件系统 wiki/*.md 的 frontmatter(source_ids
  是 YAML list 或单值;旧文件无该字段时 fallback knowledge_id)。
- ``_parse_json_list(raw)``:读 SQLite wiki_pages.source_ids(JSON string,
  如 '["k1","k2"]');容错返回 []。

供 WikiParentRetriever / WikiFsLint / WikiReadStage SQLite fallback 共享,
消除「sources 用 knowledge_id、comparisons 用 source_ids」的异构读取。
"""
from __future__ import annotations

import json


def resolve_source_ids(fm: dict) -> list[str]:
    """读 frontmatter source_ids;旧文件 fallback knowledge_id。"""
    if not isinstance(fm, dict):
        return []
    sids = fm.get("source_ids")
    if sids:
        if isinstance(sids, list):
            return [str(s) for s in sids if s]
        return [str(sids)]
    kid = fm.get("knowledge_id")
    return [str(kid)] if kid else []


def _parse_json_list(raw) -> list[str]:
    """解析 SQLite source_ids(JSON string → list);容错返回 []。"""
    if isinstance(raw, list):
        return [str(s) for s in raw if s]
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return [str(s) for s in v if s] if isinstance(v, list) else []
        except Exception:
            return []
    return []
