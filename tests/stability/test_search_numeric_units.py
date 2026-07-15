"""Phase 0/6 — 数字单位与短语混淆。"""
from __future__ import annotations

import json
from datetime import datetime

from src.services.db import Database


def _insert(title: str, content: str, kid: str) -> None:
    Database.insert_knowledge({
        "id": kid,
        "title": title,
        "content": content,
        "source_type": "manual",
        "source_path": "",
        "file_type": "md",
        "file_size": 0,
        "content_hash": kid,
        "file_created_at": "",
        "file_modified_at": "",
        "tags": "[]",
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    })
    Database.insert_blocks([{
        "id": f"blk-{kid}",
        "parent_id": None,
        "page_id": kid,
        "content": content,
        "block_type": "text",
        "properties": "{}",
        "order_idx": 0,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }])


def test_60_meters_does_not_rank_bead_per_meter_as_top(monkeypatch):
    """「60 米」不得把「60珠/米」当作有效高相关命中。"""
    _insert("灯带规格", "本产品为 60珠/米 LED 灯带，功率 12W。", "doc-beads")
    _insert("房间尺寸", "客厅长度约 60 米，宽度 20 米。", "doc-meters")
    _insert("时间参数", "超时时间 60 秒。", "doc-seconds")

    from types import SimpleNamespace

    from src.mcp.tools import retrieval

    monkeypatch.setattr(
        "src.mcp.tools.retrieval._get_container",
        lambda: SimpleNamespace(db=Database),
    )

    result = retrieval.search(query="60 米", limit=5)
    assert result["ok"] is True
    data = result.get("data") or []
    # 若有结果，top1 不应是珠/米文档
    if data:
        top = data[0]
        title = (top.get("title") or "") + (top.get("content") or top.get("text") or "")
        assert "珠/米" not in title and "珠/米" not in json.dumps(top, ensure_ascii=False)
        # 理想命中房间尺寸
        blob = json.dumps(data[:3], ensure_ascii=False)
        assert "房间" in blob or "60 米" in blob or "60米" in blob or "doc-meters" in blob


def test_six_month_no_interaction_prefers_wecom_invalid_fans(monkeypatch):
    _insert(
        "企微无效粉丝规则",
        "连续 6个月无互动 的粉丝将被标记为无效。",
        "doc-wecom",
    )
    _insert(
        "试用期制度",
        "新员工有 6个月试用期，期满考核。",
        "doc-probation",
    )
    from types import SimpleNamespace

    from src.mcp.tools import retrieval

    monkeypatch.setattr(
        "src.mcp.tools.retrieval._get_container",
        lambda: SimpleNamespace(db=Database),
    )
    result = retrieval.search(query="6个月无互动", limit=5)
    assert result["ok"] is True
    data = result.get("data") or []
    assert data, "expected at least one hit for 6个月无互动"
    top_blob = json.dumps(data[0], ensure_ascii=False)
    assert "无效" in top_blob or "企微" in top_blob or "无互动" in top_blob
    assert "试用期" not in top_blob or "无互动" in top_blob
