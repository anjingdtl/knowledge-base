"""ConflictRepository 测试"""
import pytest

from src.models.version_conflict import ConflictSession, ConflictPair, ConflictIgnore
from src.repositories.conflict_repo import ConflictRepository


@pytest.fixture
def repo():
    return ConflictRepository()


def test_create_and_get_session(repo):
    session = ConflictSession()
    repo.create_session(session)
    got = repo.get_session(session.id)
    assert got is not None
    assert got.status == "scanning"


def test_update_session_status(repo):
    session = ConflictSession()
    repo.create_session(session)
    repo.update_session_status(session.id, "ready", completed_at="2026-06-28T10:00:00")
    got = repo.get_session(session.id)
    assert got.status == "ready"
    assert got.completed_at == "2026-06-28T10:00:00"


def test_list_sessions_by_status(repo):
    s1 = ConflictSession()
    s2 = ConflictSession(status="ready")
    repo.create_session(s1)
    repo.create_session(s2)
    scanning = repo.list_sessions(status="scanning")
    ready = repo.list_sessions(status="ready")
    assert any(s.id == s1.id for s in scanning)
    assert any(s.id == s2.id for s in ready)


def test_create_and_get_pair(repo):
    session = ConflictSession()
    repo.create_session(session)
    pair = ConflictPair(
        session_id=session.id,
        item_a_id="aaa",
        item_b_id="bbb",
        candidate_source="sql_tag",
    )
    repo.create_pair(pair)
    got = repo.get_pair(pair.id)
    assert got is not None
    assert got.item_a_id == "aaa"
    assert got.pair_key == "aaa|bbb"


def test_list_pairs_with_join(repo):
    """list_pairs 应 LEFT JOIN knowledge_items 返回标题"""
    from src.models.knowledge import KnowledgeItem
    from src.repositories.knowledge_repo import KnowledgeRepository

    kr = KnowledgeRepository()
    item = KnowledgeItem(title="2022年劳动竞赛制度", content="旧版")
    kr.insert(item.to_row())

    session = ConflictSession()
    repo.create_session(session)
    pair = ConflictPair(
        session_id=session.id,
        item_a_id=item.id,
        item_b_id="nonexistent",
        candidate_source="sql_tag",
    )
    repo.create_pair(pair)

    pairs = repo.list_pairs(session.id, status="pending")
    assert len(pairs) == 1
    assert pairs[0]["item_a_title"] == "2022年劳动竞赛制度"
    assert pairs[0]["item_b_title"] is None


def test_update_pair_judgment(repo):
    session = ConflictSession()
    repo.create_session(session)
    pair = ConflictPair(session_id=session.id, item_a_id="a", item_b_id="b", candidate_source="sql_tag")
    repo.create_pair(pair)
    repo.update_pair_judgment(
        pair.id,
        relation_type="supersedes",
        newer_item_id="b",
        confidence=0.9,
        reason="2025版替代2022版",
    )
    got = repo.get_pair(pair.id)
    assert got.relation_type == "supersedes"
    assert got.confidence == 0.9
    assert got.judged_at is not None


def test_update_pair_status(repo):
    session = ConflictSession()
    repo.create_session(session)
    pair = ConflictPair(session_id=session.id, item_a_id="a", item_b_id="b", candidate_source="sql_tag")
    repo.create_pair(pair)
    repo.update_pair_status(pair.id, "deleted")
    got = repo.get_pair(pair.id)
    assert got.status == "deleted"
    assert got.resolved_at is not None


def test_add_ignore_unique_constraint(repo):
    """同 pair_key 二次插入应被忽略（INSERT OR IGNORE）"""
    ignore1 = ConflictIgnore.from_pair("a", "b")
    ignore2 = ConflictIgnore.from_pair("b", "a")  # 同 pair_key
    repo.add_ignore(ignore1)
    repo.add_ignore(ignore2)  # 应静默失败
    ignores = repo.list_ignores()
    assert len(ignores) == 1


def test_is_ignored(repo):
    ignore = ConflictIgnore.from_pair("a", "b")
    repo.add_ignore(ignore)
    assert repo.is_ignored("a", "b")
    assert repo.is_ignored("b", "a")  # 归一化后应等价
    assert not repo.is_ignored("a", "c")


def test_list_ignores_with_titles(repo):
    from src.models.knowledge import KnowledgeItem
    from src.repositories.knowledge_repo import KnowledgeRepository

    kr = KnowledgeRepository()
    item = KnowledgeItem(title="制度A", content="A")
    kr.insert(item.to_row())

    ignore = ConflictIgnore.from_pair(item.id, "other")
    repo.add_ignore(ignore)

    ignores = repo.list_ignores()
    assert len(ignores) == 1
    assert ignores[0]["item_a_title"] == "制度A"


def test_delete_ignore(repo):
    ignore = ConflictIgnore.from_pair("a", "b")
    repo.add_ignore(ignore)
    ok = repo.delete_ignore(ignore.id)
    assert ok
    assert not repo.is_ignored("a", "b")


def test_increment_session_counter(repo):
    session = ConflictSession()
    repo.create_session(session)
    repo.increment_session_counter(session.id, "candidates_found", 5)
    repo.increment_session_counter(session.id, "candidates_found", 3)
    got = repo.get_session(session.id)
    assert got.candidates_found == 8
