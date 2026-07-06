"""find_duplicates + backfill_content_hash 去重逻辑测试

覆盖场景：
- 同 content_hash 的不同标题条目应被识别为重复
- content_hash 为空但标准化标题相同的条目应被识别为重复
- backfill_content_hash 能为空哈希记录补算哈希值
- 内容不同的条目不应被误判为重复
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import pytest

from src.services.db import Database

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _insert(conn, *, title: str, content: str = "", content_hash: str = "",
            source_path: str = "", deleted_at: str | None = None):
    """直接插入一条 knowledge_items 记录，返回 id。"""
    kid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO knowledge_items
           (id, title, content, source_type, source_path, file_type, file_size,
            content_hash, file_created_at, file_modified_at, tags, version,
            created_at, updated_at, deleted_at)
           VALUES (?, ?, ?, 'manual', ?, '', 0, ?, '', '', '[]', 1, ?, ?, ?)""",
        (kid, title, content, source_path, content_hash, now, now, deleted_at),
    )
    return kid


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_tables():
    """每个测试前后清空 knowledge_items，保证隔离。"""
    conn = Database.get_conn()
    conn.execute("DELETE FROM knowledge_items")
    conn.commit()
    yield
    conn.execute("DELETE FROM knowledge_items")
    conn.commit()


# ---------------------------------------------------------------------------
# tests: find_duplicates — content_hash 策略
# ---------------------------------------------------------------------------

class TestFindDuplicatesByHash:
    """相同 content_hash 的条目应被分为一组重复。"""

    def test_same_hash_different_titles(self):
        """同内容但标题含不同 hex 后缀 → 应识别为重复。"""
        content = "合同管理办法正文内容..."
        h = hashlib.sha256(content.encode()).hexdigest()
        conn = Database.get_conn()
        _insert(conn, title="合同管理办法--00ae8a18", content=content, content_hash=h)
        _insert(conn, title="合同管理办法--cbd6981e", content=content, content_hash=h)
        conn.commit()

        groups = Database.find_duplicates()
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_same_hash_same_title(self):
        """完全相同的标题和哈希 → 应识别为重复。"""
        content = "相同内容"
        h = hashlib.sha256(content.encode()).hexdigest()
        conn = Database.get_conn()
        _insert(conn, title="同一条目", content=content, content_hash=h)
        _insert(conn, title="同一条目", content=content, content_hash=h)
        conn.commit()

        groups = Database.find_duplicates()
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_different_hash_not_deduped(self):
        """内容不同（哈希不同）的条目不应被误判。"""
        conn = Database.get_conn()
        h1 = hashlib.sha256("content_A".encode()).hexdigest()
        h2 = hashlib.sha256("content_B".encode()).hexdigest()
        _insert(conn, title="文档A", content="content_A", content_hash=h1)
        _insert(conn, title="文档B", content="content_B", content_hash=h2)
        conn.commit()

        groups = Database.find_duplicates()
        assert len(groups) == 0

    def test_deleted_items_excluded(self):
        """软删除的条目不参与去重扫描。"""
        content = "已删除的重复"
        h = hashlib.sha256(content.encode()).hexdigest()
        conn = Database.get_conn()
        _insert(conn, title="活跃条目", content=content, content_hash=h)
        _insert(conn, title="已删条目", content=content, content_hash=h, deleted_at="2026-01-01T00:00:00")
        conn.commit()

        groups = Database.find_duplicates()
        assert len(groups) == 0

    def test_group_sorted_newest_first(self):
        """每组内按 created_at 降序排列，最新在前。"""
        content = "重复内容"
        h = hashlib.sha256(content.encode()).hexdigest()
        conn = Database.get_conn()
        _insert(conn, title="旧条目", content=content, content_hash=h)
        _insert(conn, title="新条目", content=content, content_hash=h)
        conn.commit()

        groups = Database.find_duplicates()
        assert len(groups) == 1
        # 两条记录，最新在前
        assert groups[0][0]["title"] == "新条目"
        assert groups[0][1]["title"] == "旧条目"


# ---------------------------------------------------------------------------
# tests: find_duplicates — 标准化标题策略（兜底）
# ---------------------------------------------------------------------------

class TestFindDuplicatesByNormalizedTitle:
    """content_hash 为空时，剥掉 --<hex> 后缀后标题相同 → 视为重复。"""

    def test_no_hash_same_normalized_title(self):
        conn = Database.get_conn()
        _insert(conn, title="社会渠道费用标准--7d3ef339", content="", content_hash="")
        _insert(conn, title="社会渠道费用标准--8ee13658", content="", content_hash="")
        conn.commit()

        groups = Database.find_duplicates()
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_no_hash_different_titles_not_deduped(self):
        """标题不同（去掉后缀后仍不同）不应误判。"""
        conn = Database.get_conn()
        _insert(conn, title="文档A--11111111", content="", content_hash="")
        _insert(conn, title="文档B--22222222", content="", content_hash="")
        conn.commit()

        groups = Database.find_duplicates()
        assert len(groups) == 0

    def test_mixed_hash_and_no_hash(self):
        """一条有 hash，一条无 hash，但标准化标题相同且 hash 条不参与标题兜底。"""
        content = "有hash的内容"
        h = hashlib.sha256(content.encode()).hexdigest()
        conn = Database.get_conn()
        _insert(conn, title="合同--aabbccdd", content=content, content_hash=h)
        _insert(conn, title="合同--11223344", content="", content_hash="")
        conn.commit()

        # 有 hash 的单独一组（只有1条，不构成重复），无 hash 的单独一组（也只有1条）
        groups = Database.find_duplicates()
        assert len(groups) == 0


# ---------------------------------------------------------------------------
# tests: backfill_content_hash
# ---------------------------------------------------------------------------

class TestBackfillContentHash:
    """backfill_content_hash 应为空哈希记录补算 sha256。"""

    def test_backfill_empty_hash(self):
        content = "需要补算哈希的内容"
        expected_hash = hashlib.sha256(content.encode()).hexdigest()
        conn = Database.get_conn()
        kid = _insert(conn, title="测试条目", content=content, content_hash="")
        conn.commit()

        count = Database.backfill_content_hash()
        assert count == 1

        row = conn.execute(
            "SELECT content_hash FROM knowledge_items WHERE id = ?", (kid,)
        ).fetchone()
        assert row["content_hash"] == expected_hash

    def test_backfill_skips_existing_hash(self):
        """已有哈希的条目不应被覆盖。"""
        content = "有哈希的内容"
        existing_hash = "existing_hash_value_12345"
        conn = Database.get_conn()
        _insert(conn, title="有哈希条目", content=content, content_hash=existing_hash)
        conn.commit()

        count = Database.backfill_content_hash()
        assert count == 0

    def test_backfill_multiple_records(self):
        """多条空哈希记录一次性回填。"""
        conn = Database.get_conn()
        ids = []
        for i in range(5):
            kid = _insert(conn, title=f"条目{i}", content=f"内容{i}", content_hash="")
            ids.append(kid)
        conn.commit()

        count = Database.backfill_content_hash()
        assert count == 5

        for kid in ids:
            row = conn.execute(
                "SELECT content_hash FROM knowledge_items WHERE id = ?", (kid,)
            ).fetchone()
            assert row["content_hash"] != ""

    def test_backfill_then_dedup(self):
        """回填后 find_duplicates 应能发现基于哈希的重复。"""
        content = "回填后去重的内容"
        conn = Database.get_conn()
        _insert(conn, title="文档--aaa11111", content=content, content_hash="")
        _insert(conn, title="文档--bbb22222", content=content, content_hash="")
        conn.commit()

        # 回填前：两条都无 hash，靠标题兜底能找到 1 组重复
        groups_before = Database.find_duplicates()
        assert len(groups_before) == 1

        # 回填后：两条都有 hash，按 hash 策略也能找到 1 组重复
        Database.backfill_content_hash()
        groups_after = Database.find_duplicates()
        assert len(groups_after) == 1
        assert len(groups_after[0]) == 2
