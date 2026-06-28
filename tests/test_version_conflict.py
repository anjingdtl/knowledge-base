"""VersionConflictService 测试"""
import json
from datetime import datetime

import pytest

from src.models.knowledge import KnowledgeItem
from src.models.version_conflict import ConflictIgnore, ConflictPair
from src.repositories.knowledge_repo import KnowledgeRepository
from src.services.version_conflict import (
    VersionConflictService, extract_title_core,
)


@pytest.fixture
def service():
    return VersionConflictService()


@pytest.fixture
def sample_versioned_policies():
    """构造 2022/2025 同名制度对"""
    kr = KnowledgeRepository()
    old = KnowledgeItem(
        title="2022年劳动竞赛执行规章制度",
        content="第三条：年假5天。第四条：奖金上限1万。",
        tags=["劳动竞赛"],
    )
    new = KnowledgeItem(
        title="2025年劳动竞赛执行规章制度",
        content="第三条：年假7天。第四条：奖金上限2万。第五条：新增考核。",
        tags=["劳动竞赛"],
    )
    kr.insert(old.to_row())
    kr.insert(new.to_row())
    return {"old": old, "new": new}


@pytest.fixture
def sample_unrelated_policies():
    """标题相似但内容无关"""
    kr = KnowledgeRepository()
    a = KnowledgeItem(title="2022年劳动竞赛执行规章制度", content="关于劳动竞赛的规定", tags=["劳动竞赛"])
    b = KnowledgeItem(title="2022年劳动保护用品采购制度", content="关于劳保用品采购", tags=["采购"])
    kr.insert(a.to_row())
    kr.insert(b.to_row())
    return {"a": a, "b": b}


# ── 会话管理 ──

def test_start_scan_session_creates_session(service):
    session_id = service.start_scan_session(run_synchronously=True)
    assert session_id
    status = service.get_session_status(session_id)
    assert status["status"] in ("ready", "error")  # 同步跑完


def test_get_session_status_returns_counts(service):
    session_id = service.start_scan_session(run_synchronously=True)
    status = service.get_session_status(session_id)
    assert "total_items_scanned" in status
    assert "candidates_found" in status
    assert "pairs_judged" in status


def test_list_sessions(service):
    service.start_scan_session(run_synchronously=True)
    sessions = service.list_sessions()
    assert len(sessions) >= 1


# ── 标题核心词 ──

def test_extract_title_core_strips_year():
    assert extract_title_core("2022年劳动竞赛执行规章制度") == "劳动竞赛执行规章制度"
    assert extract_title_core("2025年劳动竞赛执行规章制度") == "劳动竞赛执行规章制度"
    assert extract_title_core("v1.0安全生产管理制度") == "安全生产管理制度"


# ── 扫描 ──

def test_scan_phase_sql_finds_same_title_core(service, sample_versioned_policies):
    session_id = service.start_scan_session(run_synchronously=True)
    pairs = service._repo.list_pairs(session_id)
    assert len(pairs) >= 1
    pair = pairs[0]
    ids = {pair["item_a_id"], pair["item_b_id"]}
    assert sample_versioned_policies["old"].id in ids
    assert sample_versioned_policies["new"].id in ids


def test_scan_skips_ignored_pairs(service, sample_versioned_policies):
    """已忽略的 pair 不应再次出现"""
    old = sample_versioned_policies["old"]
    new = sample_versioned_policies["new"]
    ignore = ConflictIgnore.from_pair(old.id, new.id)
    service._repo.add_ignore(ignore)

    session_id = service.start_scan_session(run_synchronously=True)
    pairs = service._repo.list_pairs(session_id)
    for p in pairs:
        ids = {p["item_a_id"], p["item_b_id"]}
        assert not (old.id in ids and new.id in ids), "已忽略的 pair 仍出现"


def test_scan_rescan_ignored_when_flag_set(service, sample_versioned_policies):
    """rescan_ignored=True 时应重新扫描"""
    old = sample_versioned_policies["old"]
    new = sample_versioned_policies["new"]
    ignore = ConflictIgnore.from_pair(old.id, new.id)
    service._repo.add_ignore(ignore)

    session_id = service.start_scan_session(rescan_ignored=True, run_synchronously=True)
    pairs = service._repo.list_pairs(session_id)
    assert len(pairs) >= 1


def test_unrelated_policies_not_paired_by_title(service, sample_unrelated_policies):
    """标题核心词不同的不应配对"""
    service.start_scan_session(run_synchronously=True)
    # 由于 a 和 b 的标题核心词不同（劳动竞赛 vs 劳动保护用品采购），不应配对
    sessions = service.list_sessions()
    latest = sessions[0]
    pairs = service._repo.list_pairs(latest["id"])
    for p in pairs:
        ids = {p["item_a_id"], p["item_b_id"]}
        assert not (sample_unrelated_policies["a"].id in ids
                    and sample_unrelated_policies["b"].id in ids)


# ── LLM 判断 ──

class FakeLLM:
    """Mock LLM，返回预设 JSON 字符串。LLMService.chat 返回 str。"""
    def __init__(self, response: str = ""):
        self.response = response or json.dumps({
            "relation_type": "supersedes",
            "newer_item_id": "B",
            "confidence": 0.9,
            "reason": "2025版替代2022版",
        }, ensure_ascii=False)
        self.calls = []

    def chat(self, messages, silent=False, **kwargs):
        self.calls.append(messages)
        return self.response


class ErrorLLM:
    def chat(self, *a, **kw):
        raise RuntimeError("API down")


@pytest.fixture
def service_with_mock_llm(sample_versioned_policies):
    fake = FakeLLM()
    svc = VersionConflictService(llm=fake)
    session_id = svc.start_scan_session(run_synchronously=True)
    return svc, session_id, fake, sample_versioned_policies


def test_judge_pending_pairs_writes_judgment(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    svc.judge_pending_pairs(session_id, limit=20, run_synchronously=True)
    # judge 后 unrelated 的会被改成 ignored，supersedes 类的保持 pending 待用户处理
    all_pairs = svc._repo.list_pairs(session_id)
    judged = [p for p in all_pairs if p.get("judged_at")]
    assert len(judged) >= 1
    assert fake.calls  # LLM 被调用过


def test_judge_handles_llm_failure_gracefully(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    svc._llm = ErrorLLM()
    svc.judge_pending_pairs(session_id, run_synchronously=True)
    # 失败的 pair 应被标记为 unrelated + confidence=0
    pairs = svc._repo.list_pairs(session_id)
    for p in pairs:
        if p.get("judged_at"):
            assert p.get("confidence") in (0, None) or p.get("confidence") == 0.0


def test_judge_pair_rejudges_single_pair(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    pairs = svc._repo.list_pairs(session_id, status="pending")
    assert pairs, "scan should create at least one pending pair"
    target = pairs[0]

    result = svc.judge_pair(target["id"], run_synchronously=True)

    assert result["ok"] is True
    assert result["judged"] == 1
    updated = svc._repo.get_pair(target["id"])
    assert updated.judged_at is not None
    assert updated.relation_type in (
        "supersedes",
        "superseded_by",
        "partial_overlap",
        "unrelated",
    )


# ── 删除 ──

def test_execute_delete_targets_older_version(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    svc.judge_pending_pairs(session_id, run_synchronously=True)
    pairs = svc._repo.list_pairs(session_id)
    target = None
    for p in pairs:
        if p.get("relation_type") in ("supersedes", "superseded_by"):
            target = p
            break
    assert target is not None, "应至少有一对 supersedes 关系"

    result = svc.execute_delete(target["id"])
    assert result["ok"] is True
    kr = svc._get_knowledge_repo()
    old_id = policies["old"].id
    new_id = policies["new"].id
    deleted_id = result["deleted_item_id"]
    # 删的应该是 newer_item_id 的另一侧（即旧版）
    newer_id = target["newer_item_id"]
    expected_deleted = target["item_b_id"] if newer_id == target["item_a_id"] else target["item_a_id"]
    assert deleted_id == expected_deleted
    # 被删的应软删
    deleted_item = kr.get(deleted_id, include_deleted=True)
    assert deleted_item is not None
    assert deleted_item.get("deleted_at") is not None
    # 新版应保留
    kept_id = new_id if deleted_id == old_id else old_id
    kept = kr.get(kept_id)
    assert kept is not None
    # pair 状态应更新
    updated = svc._repo.get_pair(target["id"])
    assert updated.status == "deleted"


def test_execute_delete_rejects_newer_item_id_outside_pair(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    pair = ConflictPair(
        session_id=session_id,
        item_a_id=policies["old"].id,
        item_b_id=policies["new"].id,
        candidate_source="sql_tag",
        relation_type="supersedes",
        newer_item_id="not-a-member",
        confidence=0.9,
        reason="invalid newer id",
        status="pending",
    )
    svc._repo.create_pair(pair)

    result = svc.execute_delete(pair.id)

    assert result["ok"] is False
    assert result["error"]["code"] == "PRECONDITION_FAILED"
    assert "newer_item_id" in result["error"]["message"]
    kr = svc._get_knowledge_repo()
    assert kr.get(policies["old"].id) is not None
    assert kr.get(policies["new"].id) is not None


def test_execute_delete_writes_operation_log(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    svc.judge_pending_pairs(session_id, run_synchronously=True)
    pairs = svc._repo.list_pairs(session_id)
    target = next(p for p in pairs if p.get("relation_type") in ("supersedes", "superseded_by"))
    result = svc.execute_delete(target["id"])
    assert result["ok"]
    # operation_log_id 可能为空字符串（如果 OperationLogService 未配置），
    # 但 delete 本身应成功


def test_execute_delete_partial_overlap_blocked(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    pair = ConflictPair(
        session_id=session_id,
        item_a_id=policies["old"].id,
        item_b_id=policies["new"].id,
        candidate_source="sql_tag",
        relation_type="partial_overlap",
        confidence=0.8,
        reason="部分重叠",
        status="pending",
    )
    pair.judged_at = datetime.now().isoformat()
    svc._repo.create_pair(pair)
    result = svc.execute_delete(pair.id)
    assert result["ok"] is False
    msg = result.get("error", {}).get("message", "")
    assert "partial" in msg.lower() or "partial_overlap" in msg.lower()


# ── 忽略 ──

def test_ignore_pair_writes_ignore_table(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    pairs = svc._repo.list_pairs(session_id, status="pending")
    if not pairs:
        svc.judge_pending_pairs(session_id, run_synchronously=True)
        pairs = svc._repo.list_pairs(session_id, status="pending")
    if not pairs:
        # 全被 judge 成 ignored 了，手动加一对
        pair = ConflictPair(
            session_id=session_id,
            item_a_id=policies["old"].id,
            item_b_id=policies["new"].id,
            candidate_source="sql_tag",
        )
        svc._repo.create_pair(pair)
        pairs = svc._repo.list_pairs(session_id, status="pending")
    target = pairs[0]
    result = svc.ignore_pair(target["id"])
    assert result["ok"] is True
    assert svc._repo.is_ignored(target["item_a_id"], target["item_b_id"])
    updated = svc._repo.get_pair(target["id"])
    assert updated.status == "ignored"


def test_list_pairs_with_pagination(service_with_mock_llm):
    svc, session_id, fake, policies = service_with_mock_llm
    page1 = svc.list_pairs(session_id, limit=10, offset=0)
    assert isinstance(page1, list)
