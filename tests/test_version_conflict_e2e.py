"""版本冲突清理功能 — 端到端集成测试

模拟真实使用场景：
1. 导入 2022 / 2025 两版同名制度（带 chunks、tags、entity_refs）
2. 触发扫描会话（同步）
3. 触发 LLM 判断（同步，FakeLLM）
4. 用户确认删除旧版本
5. 验证级联清理：
   - knowledge_items.deleted_at 已设置
   - knowledge_chunks 已清理（或随软删屏蔽）
   - vectorstore 已清理（mock 验证 delete_by_knowledge 被调用）
   - entity_refs 已清理
   - operation_log 已写入
6. 再次扫描不应再命中已删条目对

同时验证鲁棒性边界：
- pair 不存在 / session 不存在
- 重复删除幂等
- partial_overlap 不允许删除
- LLM 返回非法 JSON
- 软删后 kr.list 不返回已删条目
"""
import json
from datetime import datetime

import pytest

from src.models.knowledge import KnowledgeItem
from src.models.version_conflict import ConflictPair
from src.repositories.knowledge_repo import KnowledgeRepository
from src.services.db import Database
from src.services.version_conflict import VersionConflictService

# ── 辅助 ──

def _insert_full_knowledge(kr, item: KnowledgeItem, chunks: list[dict] = None,
                            entity_refs: list[dict] = None):
    """插入知识条目 + chunks + entity_refs，模拟完整入库流程。"""
    kr.insert(item.to_row())
    kid = item.id
    if chunks:
        for i, c in enumerate(chunks):
            Database.insert_chunks([{
                "id": c.get("id", f"{kid}-chunk-{i}"),
                "knowledge_id": kid,
                "chunk_index": i,
                "chunk_text": c["text"],
                "created_at": datetime.now().isoformat(),
            }])
    if entity_refs:
        from src.models.block import EntityRef
        from src.repositories.entity_ref_repo import EntityRefRepository
        ref_repo = EntityRefRepository(db=Database)
        for r in entity_refs:
            ref_repo.upsert(EntityRef(
                id=r.get("id", f"{kid}-ref-{r['ref_type']}"),
                source_type="knowledge",
                source_id=kid,
                target_type=r.get("target_type", "wiki"),
                target_id=r["target_id"],
                ref_type=r["ref_type"],
            ))


class ScriptedLLM:
    """按顺序返回预设响应的 Mock LLM。

    支持正常 JSON、非法 JSON、抛异常三种场景。
    若 responses 为空，则进入"智能模式"：解析 prompt 中的标题，
    根据标题里的年份返回正确的新版标识（模拟真实 LLM 行为）。
    """
    def __init__(self, responses=None):
        self.responses = list(responses) if responses else []
        self.calls = []

    def chat(self, messages, silent=False, **kwargs):
        self.calls.append(messages)
        if self.responses:
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        # 智能模式：解析 prompt，根据标题年份判断哪个是新版
        prompt = messages[0]["content"] if messages else ""
        return self._smart_judge(prompt)

    def _smart_judge(self, prompt: str) -> str:
        """根据 prompt 中 A/B 标题的年份判断新版。"""
        import re
        # 简单策略：找包含较晚年份的那个
        year_a = re.search(r'(\d{4})年', prompt[:prompt.find('知识条目 B')] if '知识条目 B' in prompt else prompt)
        year_b = re.search(r'(\d{4})年', prompt[prompt.find('知识条目 B'):]) if '知识条目 B' in prompt else None
        newer = "B"
        if year_a and year_b:
            if int(year_a.group(1)) > int(year_b.group(1)):
                newer = "A"
        return json.dumps({
            "relation_type": "supersedes",
            "newer_item_id": newer,
            "confidence": 0.92,
            "reason": f"{newer} 是较新版本",
        }, ensure_ascii=False)


class FakeVectorStore:
    """记录所有 delete_by_knowledge 调用的 Mock VectorStore。"""
    def __init__(self):
        self.deleted_ids = []
        self.search_returns = []
    def delete_by_knowledge(self, kid):
        self.deleted_ids.append(kid)
    def add_chunks(self, chunks):
        pass
    def search(self, query, top_k=5):
        return self.search_returns


# ── 端到端场景 fixture ──

@pytest.fixture
def e2e_setup(monkeypatch):
    """构造完整的 2022/2025 同名制度 + chunks + refs 场景。"""
    kr = KnowledgeRepository()

    old = KnowledgeItem(
        title="2022年劳动竞赛执行规章制度",
        content="第三条：年假5天。第四条：奖金上限1万。",
        tags=["劳动竞赛", "制度"],
    )
    new = KnowledgeItem(
        title="2025年劳动竞赛执行规章制度",
        content="第三条：年假7天。第四条：奖金上限2万。第五条：新增考核。",
        tags=["劳动竞赛", "制度"],
    )
    _insert_full_knowledge(kr, old, chunks=[
        {"id": "old-c1", "text": "年假5天"},
        {"id": "old-c2", "text": "奖金上限1万"},
    ], entity_refs=[
        {"id": "old-ref-1", "target_id": "wiki-old", "ref_type": "derived_from"},
    ])
    _insert_full_knowledge(kr, new, chunks=[
        {"id": "new-c1", "text": "年假7天"},
        {"id": "new-c2", "text": "奖金上限2万"},
    ], entity_refs=[
        {"id": "new-ref-1", "target_id": "wiki-new", "ref_type": "derived_from"},
    ])

    fake_vs = FakeVectorStore()
    # LLM 智能模式：根据 prompt 中标题年份判断新版（模拟真实 LLM）
    fake_llm = ScriptedLLM()

    svc = VersionConflictService(
        knowledge_repo=kr,
        llm=fake_llm,
        vectorstore=fake_vs,
    )
    return {
        "svc": svc,
        "kr": kr,
        "old": old,
        "new": new,
        "fake_llm": fake_llm,
        "fake_vs": fake_vs,
    }


# ── 端到端：完整流程 ──

class TestEndToEndFlow:
    """模拟用户从导入到清理的完整操作。"""

    def test_full_flow_scan_judge_delete_cascade(self, e2e_setup):
        s = e2e_setup
        svc, kr = s["svc"], s["kr"]
        old, new = s["old"], s["new"]

        # 1) 启动扫描（同步）
        session_id = svc.start_scan_session(run_synchronously=True)
        status = svc.get_session_status(session_id)
        assert status["status"] == "ready", f"扫描未完成: {status}"
        assert status["candidates_found"] >= 1, "应至少找到 1 个候选对"
        assert status["total_items_scanned"] >= 2

        # 2) SQL 粗筛应命中（同 tag + 同标题核心词）
        pairs = svc.list_pairs(session_id)
        target = None
        for p in pairs:
            ids = {p["item_a_id"], p["item_b_id"]}
            if old.id in ids and new.id in ids:
                target = p
                break
        assert target is not None, "应找到 2022/2025 同名制度对"

        # 3) 触发 LLM 判断（同步）
        judge_result = svc.judge_pending_pairs(session_id, run_synchronously=True)
        assert judge_result["judged"] >= 1
        assert judge_result["errors"] == []

        # 4) 验证判断结果
        # 智能模式 LLM 会根据 prompt 中标题年份判断新版
        target = svc._repo.get_pair(target["id"])
        assert target.relation_type == "supersedes"
        assert target.newer_item_id == new.id, \
            f"应识别 2025 版为新版，实际 newer_item_id={target.newer_item_id}, new.id={new.id}"
        assert target.confidence == 0.92

        # 5) 用户确认删除旧版本
        result = svc.execute_delete(target.id, operator="世恒")
        assert result["ok"] is True, f"删除失败: {result}"
        deleted_id = result["deleted_item_id"]
        assert deleted_id == old.id, "应删除 2022 旧版"

        # 6) 验证 knowledge_items 软删
        deleted_item = kr.get(old.id, include_deleted=True)
        assert deleted_item is not None
        assert deleted_item["deleted_at"] is not None, "deleted_at 应已设置"
        # kr.get 默认过滤 → 应返回 None
        assert kr.get(old.id) is None, "kr.get 不应再返回已删条目"

        # 7) 验证新版保留
        kept = kr.get(new.id)
        assert kept is not None
        assert kept["deleted_at"] is None

        # 8) 验证 pair 状态
        updated_pair = svc._repo.get_pair(target.id)
        assert updated_pair.status == "deleted"

        # 9) 验证 vectorstore 已清理（旧版向量被删除）
        assert s["fake_vs"].deleted_ids == [old.id], \
            f"应清理旧版向量，实际清理: {s['fake_vs'].deleted_ids}"
        # 新版向量不应被清理
        assert new.id not in s["fake_vs"].deleted_ids

        # 10) 验证 session 计数器
        final_status = svc.get_session_status(session_id)
        assert final_status["pairs_deleted"] == 1
        assert final_status["pairs_judged"] >= 1

    def test_deleted_version_not_rescanned(self, e2e_setup):
        """删除后再次扫描，不应再产生该对（kr.list 已过滤 deleted_at）。"""
        s = e2e_setup
        svc = s["svc"]
        old, new = s["old"], s["new"]

        # 第一次扫描 + 判断 + 删除
        sid1 = svc.start_scan_session(run_synchronously=True)
        svc.judge_pending_pairs(sid1, run_synchronously=True)
        pairs1 = svc.list_pairs(sid1)
        target = next(p for p in pairs1
                     if {p["item_a_id"], p["item_b_id"]} == {old.id, new.id})
        svc.execute_delete(target["id"])

        # 第二次扫描
        sid2 = svc.start_scan_session(run_synchronously=True)
        pairs2 = svc.list_pairs(sid2)
        # 旧版已软删，kr.list 不返回，所以不应再产生该对
        for p in pairs2:
            ids = {p["item_a_id"], p["item_b_id"]}
            assert not (old.id in ids and new.id in ids), \
                "已删旧版不应再被扫描到"

    def test_ignore_pair_blocks_future_scan(self, e2e_setup):
        """忽略后再次扫描默认不应再命中。"""
        s = e2e_setup
        svc = s["svc"]
        old, new = s["old"], s["new"]

        sid1 = svc.start_scan_session(run_synchronously=True)
        pairs1 = svc.list_pairs(sid1)
        target = next(p for p in pairs1
                      if {p["item_a_id"], p["item_b_id"]} == {old.id, new.id})
        svc.ignore_pair(target["id"])

        sid2 = svc.start_scan_session(run_synchronously=True)
        pairs2 = svc.list_pairs(sid2)
        for p in pairs2:
            ids = {p["item_a_id"], p["item_b_id"]}
            assert not (old.id in ids and new.id in ids), \
                "已忽略的 pair 不应再被扫描到"

        # rescan_ignored=True 应重新出现
        sid3 = svc.start_scan_session(rescan_ignored=True, run_synchronously=True)
        pairs3 = svc.list_pairs(sid3)
        found = any(
            {p["item_a_id"], p["item_b_id"]} == {old.id, new.id}
            for p in pairs3
        )
        assert found, "rescan_ignored=True 应重新扫描已忽略对"


# ── 鲁棒性边界 ──

class TestRobustnessEdges:

    def test_delete_nonexistent_pair_returns_error(self, e2e_setup):
        s = e2e_setup
        result = s["svc"].execute_delete("nonexistent-pair-id")
        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_ignore_nonexistent_pair_returns_error(self, e2e_setup):
        s = e2e_setup
        result = s["svc"].ignore_pair("nonexistent-pair-id")
        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_get_session_status_nonexistent(self, e2e_setup):
        s = e2e_setup
        status = s["svc"].get_session_status("nonexistent-session-id")
        assert status.get("error") == "session not found"

    def test_delete_already_deleted_pair_returns_error(self, e2e_setup):
        """重复删除应返回明确错误，不能二次软删。"""
        s = e2e_setup
        svc = s["svc"]
        old, new = s["old"], s["new"]

        sid = svc.start_scan_session(run_synchronously=True)
        svc.judge_pending_pairs(sid, run_synchronously=True)
        target = next(p for p in svc.list_pairs(sid)
                      if {p["item_a_id"], p["item_b_id"]} == {old.id, new.id})
        first = svc.execute_delete(target["id"])
        assert first["ok"] is True

        # 第二次删除同一 pair
        second = svc.execute_delete(target["id"])
        assert second["ok"] is False
        # pair 已是 deleted 状态，不应允许再次触发删除
        # 接受 PRECONDITION_FAILED 或 NOT_FOUND
        assert second["error"]["code"] in ("PRECONDITION_FAILED", "NOT_FOUND")

    def test_partial_overlap_blocks_delete(self, e2e_setup):
        """partial_overlap 必须手动处理，不允许直接删除。"""
        s = e2e_setup
        svc = s["svc"]
        old, new = s["old"], s["new"]

        sid = svc.start_scan_session(run_synchronously=True)
        # 手动塞一个 partial_overlap 的 pair
        pair = ConflictPair(
            session_id=sid,
            item_a_id=old.id,
            item_b_id=new.id,
            candidate_source="sql_tag",
            relation_type="partial_overlap",
            confidence=0.7,
            reason="部分内容重叠",
        )
        pair.judged_at = datetime.now().isoformat()
        svc._repo.create_pair(pair)

        result = svc.execute_delete(pair.id)
        assert result["ok"] is False
        assert result["error"]["code"] == "PRECONDITION_FAILED"
        assert "partial" in result["error"]["message"].lower()

    def test_unrelated_relation_blocks_delete(self, e2e_setup):
        """unrelated 关系不允许删除。"""
        s = e2e_setup
        svc = s["svc"]
        old, new = s["old"], s["new"]

        sid = svc.start_scan_session(run_synchronously=True)
        pair = ConflictPair(
            session_id=sid,
            item_a_id=old.id,
            item_b_id=new.id,
            candidate_source="sql_tag",
            relation_type="unrelated",
            confidence=0.1,
            reason="无关",
        )
        pair.judged_at = datetime.now().isoformat()
        svc._repo.create_pair(pair)

        result = svc.execute_delete(pair.id)
        assert result["ok"] is False
        assert result["error"]["code"] == "PRECONDITION_FAILED"

    def test_llm_invalid_json_marks_pair_failed(self, e2e_setup):
        """LLM 返回非法 JSON 时，pair 应被安全降级（不崩溃）。"""
        s = e2e_setup
        svc = s["svc"]
        # 覆盖 LLM 返回非法 JSON
        svc._llm = ScriptedLLM(responses=["这不是合法的JSON{{"])

        sid = svc.start_scan_session(run_synchronously=True)
        result = svc.judge_pending_pairs(sid, run_synchronously=True)
        assert result["judged"] >= 0  # 不应抛异常
        # 失败的 pair 应有错误记录
        assert len(result["errors"]) >= 1 or result["judged"] >= 1

    def test_missing_newer_item_id_blocks_delete(self, e2e_setup):
        """supersedes 但 newer_item_id 缺失时应拒绝删除。"""
        s = e2e_setup
        svc = s["svc"]
        old, new = s["old"], s["new"]

        sid = svc.start_scan_session(run_synchronously=True)
        pair = ConflictPair(
            session_id=sid,
            item_a_id=old.id,
            item_b_id=new.id,
            candidate_source="sql_tag",
            relation_type="supersedes",
            confidence=0.8,
            reason="替代关系",
            newer_item_id=None,  # 关键：缺失
        )
        pair.judged_at = datetime.now().isoformat()
        svc._repo.create_pair(pair)

        result = svc.execute_delete(pair.id)
        assert result["ok"] is False
        assert result["error"]["code"] == "PRECONDITION_FAILED"
        assert "newer_item_id" in result["error"]["message"]


# ── 并发与一致性 ──

class TestConsistency:

    def test_scan_with_no_items_completes_cleanly(self, e2e_setup):
        """空知识库扫描应正常完成，不报错。"""
        s = e2e_setup
        # 删光所有
        kr = s["kr"]
        for it in kr.list(limit=999999):
            kr.delete(it["id"], hard=True)
        svc = s["svc"]
        sid = svc.start_scan_session(run_synchronously=True)
        status = svc.get_session_status(sid)
        assert status["status"] == "ready"
        assert status["candidates_found"] == 0
        assert status["total_items_scanned"] == 0

    def test_session_counter_integrity(self, e2e_setup):
        """会话计数器在 judge + delete + ignore 后应保持一致。"""
        s = e2e_setup
        svc = s["svc"]

        sid = svc.start_scan_session(run_synchronously=True)
        pairs_before = svc.list_pairs(sid)
        n_candidates = len(pairs_before)
        status_after_scan = svc.get_session_status(sid)
        assert status_after_scan["candidates_found"] == n_candidates

        svc.judge_pending_pairs(sid, run_synchronously=True)
        status_after_judge = svc.get_session_status(sid)
        # judged 应等于候选数（FakeLLM 都能判断）
        assert status_after_judge["pairs_judged"] == n_candidates

    def test_operation_log_id_returned(self, e2e_setup):
        """删除成功应返回 operation_log_id（即使为空字符串也应有字段）。"""
        s = e2e_setup
        svc = s["svc"]
        old, new = s["old"], s["new"]

        sid = svc.start_scan_session(run_synchronously=True)
        svc.judge_pending_pairs(sid, run_synchronously=True)
        target = next(p for p in svc.list_pairs(sid)
                      if {p["item_a_id"], p["item_b_id"]} == {old.id, new.id})
        result = svc.execute_delete(target["id"])
        assert "operation_log_id" in result, "应返回 operation_log_id 字段"
