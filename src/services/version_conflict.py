"""版本冲突扫描与清理编排服务

借鉴 Obsidian Repeat 插件的"扫描 → 提示用户 → 确认后执行"工作流，
用于公司制度库版本迭代场景（如 2022版 vs 2025版同名制度并存）。

核心流程：
  1. SQL 粗筛（tag/分类/标题核心词）
  2. embedding 补充（vectorstore.search，阈值 0.85+）
  3. LLM 四类关系判断
  4. 用户确认 → 软删旧版 → operation_log 支持 undo

不引入 cron，由用户主动触发。
"""
import json
import logging
import re
import time

from src.models.version_conflict import (
    ConflictIgnore,
    ConflictPair,
    ConflictSession,
    _make_pair_key,
)
from src.repositories.conflict_repo import ConflictRepository
from src.services.db import Database
from src.utils.llm_text import strip_think

logger = logging.getLogger(__name__)


# 性能保护配置（适配 embedding 2000 RPM / 500K TPM，保守留余量）
EMBEDDING_QPS = 3                    # 每秒 3 次 = 180 RPM（仅用 9% 配额）
EMBEDDING_QUERY_MAX_TOKENS = 500
LLM_BATCH_SIZE = 20
MAX_CANDIDATES_PER_SESSION = 50
EMBEDDING_SIMILARITY_THRESHOLD = 0.85


# 标题核心词提取：去掉年份/版本号前缀
_TITLE_PREFIX_PATTERNS = [
    re.compile(r'^\d{4}\s*年'),       # "2022年"
    re.compile(r'^\[?v?\d+\.\d+\]?', re.IGNORECASE),  # "v1.0" / "[v2.0]"
    re.compile(r'^第?\d+[版次]', re.IGNORECASE),       # "第一版" / "第2次"
]


def extract_title_core(title: str) -> str:
    """提取标题核心词，去掉年份/版本号前缀。
    例：'2022年劳动竞赛执行规章制度' → '劳动竞赛执行规章制度'
    """
    core = title.strip()
    changed = True
    while changed:
        changed = False
        for pat in _TITLE_PREFIX_PATTERNS:
            new = pat.sub('', core).strip()
            if new != core:
                core = new
                changed = True
    return core


JUDGE_PROMPT = """你是公司制度文档版本分析专家。判断以下两条知识是否为同一制度的不同版本。

## 知识条目 A
- ID: {id_a}
- 标题: {title_a}
- 创建时间: {created_a}
- 内容摘要: {content_a}

## 知识条目 B
- ID: {id_b}
- 标题: {title_b}
- 创建时间: {created_b}
- 内容摘要: {content_b}

## 判断要求
分析两条目关系，从以下四类中选一种：
- supersedes: A 是 B 的新版本（B 已被 A 替代）
- superseded_by: B 是 A 的新版本（A 已被 B 替代）
- partial_overlap: 部分内容重叠，但非整版本迭代
- unrelated: 无版本关系

## 输出格式（严格 JSON，不要 ```json 标记）
{{"relation_type":"supersedes|superseded_by|partial_overlap|unrelated","newer_item_id":"A或B的ID（仅 supersedes/superseded_by 时填，否则空字符串）","confidence":0.0-1.0,"reason":"一句话说明判断依据"}}
"""


class VersionConflictService:
    """版本冲突扫描与清理编排服务"""

    def __init__(self, repo=None, knowledge_repo=None, llm=None, vectorstore=None, blockstore=None):
        self._repo = repo or ConflictRepository()
        self._knowledge_repo = knowledge_repo
        self._llm = llm
        self._vectorstore = vectorstore
        self._blockstore = blockstore

    def _get_knowledge_repo(self):
        if self._knowledge_repo is None:
            from src.repositories.knowledge_repo import KnowledgeRepository
            self._knowledge_repo = KnowledgeRepository()
        return self._knowledge_repo

    def _get_llm(self):
        if self._llm is None:
            from src.services.llm import LLMService
            self._llm = LLMService()
        return self._llm

    def _get_vectorstore(self):
        if self._vectorstore is None:
            from src.services.vectorstore import VectorStore
            self._vectorstore = VectorStore()
        return self._vectorstore

    def _get_block_store(self):
        """Block 级向量存储。用户知识库实际索引在 vec_blocks(block 级),
        而非 vec_chunks(知识级),故版本冲突扫描改用 BlockStore。"""
        if self._blockstore is None:
            from src.services.block_store import BlockStore
            from src.services.db import Database
            self._blockstore = BlockStore(db=Database)
        return self._blockstore

    def _get_operation_log_service(self):
        """从 AppContainer 获取 OperationLogService（已注入 repo）。"""
        try:
            # 优先从全局 container 获取
            from src.api.deps import get_container
            container = get_container()
            return container.operation_log
        except Exception:
            # 降级：自己构造
            from src.repositories.operation_log_repo import OperationLogRepository
            from src.services.operation_log import OperationLogService
            return OperationLogService(
                repo=OperationLogRepository(),
                knowledge_repo=self._get_knowledge_repo(),
            )

    # ── 会话管理 ──

    def start_scan_session(self, rescan_ignored: bool = False,
                           run_synchronously: bool = False) -> str:
        """创建扫描会话。默认异步执行；run_synchronously=True 用于测试。

        Args:
            rescan_ignored: True 时重新扫描已忽略对（默认 False）
            run_synchronously: True 时同步跑完整个扫描（测试用）

        Returns:
            session_id
        """
        session = ConflictSession()
        self._repo.create_session(session)

        if run_synchronously:
            try:
                self._run_scan(session.id, rescan_ignored=rescan_ignored)
            except Exception as e:
                logger.exception("Scan failed for session %s", session.id)
                self._repo.update_session_status(
                    session.id, "error", error=str(e)
                )
        else:
            # 异步：通过 AsyncTaskService 创建 job
            try:
                from src.services.async_task import AsyncTaskService
                AsyncTaskService.create_job(
                    "version_conflict_scan",
                    {"session_id": session.id, "rescan_ignored": rescan_ignored},
                    priority=1,
                    max_retries=0,
                )
            except Exception as e:
                # AsyncTaskService 不可用时降级同步
                logger.warning("AsyncTaskService unavailable, running sync: %s", e)
                try:
                    self._run_scan(session.id, rescan_ignored=rescan_ignored)
                except Exception as e2:
                    logger.exception("Sync scan failed for session %s", session.id)
                    self._repo.update_session_status(
                        session.id, "error", error=str(e2)
                    )

        return session.id

    def get_session_status(self, session_id: str) -> dict:
        """查询会话进度。"""
        session = self._repo.get_session(session_id)
        if not session:
            return {"error": "session not found", "session_id": session_id}
        counts = self._repo.count_pairs_by_status(session_id)
        return {
            "session_id": session.id,
            "status": session.status,
            "total_items_scanned": session.total_items_scanned,
            "candidates_found": session.candidates_found,
            "pairs_judged": session.pairs_judged,
            "pairs_deleted": session.pairs_deleted,
            "pairs_ignored": session.pairs_ignored,
            "error": session.error,
            "started_at": session.started_at,
            "completed_at": session.completed_at,
            "pairs_by_status": counts,
        }

    def list_sessions(self, status: str | None = None,
                      limit: int = 50, offset: int = 0) -> list[dict]:
        """列出会话。"""
        sessions = self._repo.list_sessions(status=status, limit=limit, offset=offset)
        return [s.to_row() for s in sessions]

    # ── 扫描阶段 ──

    def _run_scan(self, session_id: str, rescan_ignored: bool = False) -> None:
        """执行完整扫描流程（同步）。由 start_scan_session 调用。"""
        self._repo.update_session_status(session_id, "scanning")

        # Phase 1: SQL 粗筛
        sql_pairs = self._scan_phase_sql(session_id, rescan_ignored=rescan_ignored)

        # Phase 2: embedding 补充
        emb_pairs = self._scan_phase_embedding(session_id, sql_pairs, rescan_ignored=rescan_ignored)

        # 合并、去重、排序(同名 title 优先,embedding 按相似度降序)、截断
        all_pairs = self._rank_and_trim(self._dedupe_pairs(sql_pairs + emb_pairs))

        # 批量写入
        if all_pairs:
            pair_objs = [ConflictPair(
                session_id=session_id,
                item_a_id=p["a"],
                item_b_id=p["b"],
                candidate_source=p["source"],
                similarity_score=p.get("similarity"),
            ) for p in all_pairs]
            self._repo.create_pairs_batch(pair_objs)
            self._repo.increment_session_counter(session_id, "candidates_found", len(all_pairs))

        # 统计扫描条目数
        kr = self._get_knowledge_repo()
        active_items = kr.list(limit=999999)
        self._repo.increment_session_counter(
            session_id, "total_items_scanned", len(active_items)
        )

        self._repo.update_session_status(session_id, "ready")

    def _dedupe_pairs(self, pairs: list[dict]) -> list[dict]:
        """按 pair_key 去重"""
        seen = set()
        out = []
        for p in pairs:
            key = _make_pair_key(p["a"], p["b"])
            if key not in seen:
                seen.add(key)
                out.append(p)
        return out

    def _rank_and_trim(self, pairs: list[dict]) -> list[dict]:
        """排序并截断:同名标题(sql_title)候选优先,embedding 候选按相似度降序。

        同名制度(标题核心词相同)是强信号,即使内容因年份差异导致 embedding 相似度
        不高也应优先保留;embedding 候选则按 cosine 相似度从高到低排。
        """
        def sort_key(p: dict) -> tuple[int, float]:
            if p.get("source") == "sql_title":
                return (0, 1.0)  # 同名制度:强信号,最优先
            return (1, -(p.get("similarity") or 0.0))
        ranked = sorted(pairs, key=sort_key)
        if len(ranked) > MAX_CANDIDATES_PER_SESSION:
            logger.info("Session candidates %d trimmed to top %d",
                        len(ranked), MAX_CANDIDATES_PER_SESSION)
        return ranked[:MAX_CANDIDATES_PER_SESSION]

    def _scan_phase_sql(self, session_id: str, rescan_ignored: bool = False) -> list[dict]:
        """SQL 粗筛：按 tag + 标题核心词分组。"""
        kr = self._get_knowledge_repo()
        items = kr.list(limit=999999)
        if len(items) < 2:
            return []

        candidates = []

        # 按"标题核心词"分组 — 同名制度不同版本(如 2022 vs 2025 同名制度)。
        # tag 笛卡尔积(同 tag 两两配对)噪音过大,已移除;内容相似度由 embedding 阶段负责。
        title_groups: dict[str, list[dict]] = {}
        for it in items:
            core = extract_title_core(it.get("title", ""))
            if core and len(core) >= 4:  # 太短的核心词会误配
                title_groups.setdefault(core, []).append(it)
        for core, group in title_groups.items():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if self._should_skip_pair(a["id"], b["id"], rescan_ignored):
                        continue
                    candidates.append({
                        "a": min(a["id"], b["id"]),
                        "b": max(a["id"], b["id"]),
                        "source": "sql_title", "similarity": None,
                    })

        return self._dedupe_pairs(candidates)

    def _should_skip_pair(self, a_id: str, b_id: str, rescan_ignored: bool) -> bool:
        """判断是否应跳过该对（已忽略）"""
        if rescan_ignored:
            return False
        return self._repo.is_ignored(a_id, b_id)

    def _scan_phase_embedding(self, session_id: str, sql_pairs: list[dict],
                              rescan_ignored: bool = False) -> list[dict]:
        """embedding 全量扫描:用 BlockStore 查 top-k 相似 block,按 knowledge_id 聚合。

        用户知识库实际索引在 vec_blocks(block 级),而非 vec_chunks(知识级),故此处
        改用 BlockStore,并按 page_id(=knowledge_id)把同知识的多个 block 命中聚合。
        """
        try:
            bs = self._get_block_store()
        except Exception as e:
            logger.warning("BlockStore unavailable, skipping embedding phase: %s", e)
            return []

        kr = self._get_knowledge_repo()
        items = kr.list(limit=999999)
        if len(items) < 2:
            return []

        candidates = []
        seen_keys: set[str] = set()  # 本阶段内部去重

        for it in items:
            content = (it.get("content") or "")[:EMBEDDING_QUERY_MAX_TOKENS]
            if not content.strip():
                continue
            try:
                similar = bs.search(query=content, top_k=8)
            except Exception as e:
                logger.warning("BlockStore.search failed for %s: %s", it["id"], e)
                continue
            # block 级 hit 按 page_id(=knowledge_id)聚合,取最小 distance(最高相似度)
            best: dict[str, float] = {}
            for hit in similar:
                hit_id = (hit.get("metadata") or {}).get("page_id") or hit.get("id")
                if not hit_id or hit_id == it["id"]:
                    continue
                distance = hit.get("distance", 1.0)
                if distance < best.get(hit_id, float("inf")):
                    best[hit_id] = distance
            for hit_id, distance in best.items():
                # distance 是 cosine distance (0-2),转 similarity score (0-1)
                hit_score = max(0.0, 1.0 - distance / 2.0)
                if hit_score < EMBEDDING_SIMILARITY_THRESHOLD:
                    continue
                if self._should_skip_pair(it["id"], hit_id, rescan_ignored):
                    continue
                pair_key = _make_pair_key(it["id"], hit_id)
                if pair_key in seen_keys:
                    continue
                candidates.append({
                    "a": min(it["id"], hit_id),
                    "b": max(it["id"], hit_id),
                    "source": "embedding",
                    "similarity": hit_score,
                })
                seen_keys.add(pair_key)
            # 限流
            time.sleep(1.0 / EMBEDDING_QPS)

        return self._dedupe_pairs(candidates)

    # ── 判断阶段 ──

    def judge_pending_pairs(self, session_id: str, limit: int = LLM_BATCH_SIZE,
                            run_synchronously: bool = False) -> dict:
        """对 session 内 pending 候选对跑 LLM 判断。

        Args:
            session_id: 会话 ID
            limit: 单次判断上限
            run_synchronously: True 时同步执行（测试用）

        Returns:
            {"judged": N, "errors": [...]}
        """
        if not run_synchronously:
            try:
                from src.services.async_task import AsyncTaskService
                AsyncTaskService.create_job(
                    "version_conflict_judge",
                    {"session_id": session_id, "limit": limit},
                    priority=1, max_retries=0,
                )
                return {"ok": True, "async": True}
            except Exception as e:
                logger.warning("AsyncTaskService unavailable, running sync: %s", e)

        self._repo.update_session_status(session_id, "judging")
        pairs = self._repo.list_pending_pairs(session_id, limit=limit)
        result = self._judge_pairs(session_id, pairs)
        self._repo.update_session_status(session_id, "ready")
        return result

    def judge_pair(self, pair_id: str, run_synchronously: bool = True) -> dict:
        """重新判断单个候选对。pair-level API 使用同步执行，避免额外 job 类型。"""
        pair = self._repo.get_pair(pair_id)
        if not pair:
            return {"ok": False, "error": {"code": "NOT_FOUND",
                                             "message": f"pair 不存在: {pair_id}"}}

        self._repo.update_session_status(pair.session_id, "judging")
        result = self._judge_pairs(pair.session_id, [pair])
        self._repo.update_session_status(pair.session_id, "ready")
        return {"ok": True, **result}

    def _judge_pairs(self, session_id: str, pairs: list[ConflictPair]) -> dict:
        """执行 LLM 判断的共享实现。"""
        kr = self._get_knowledge_repo()
        llm = self._get_llm()
        judged = 0
        errors = []
        items_cache = {}

        for pair in pairs:
            try:
                if pair.item_a_id not in items_cache:
                    items_cache[pair.item_a_id] = kr.get(pair.item_a_id) or {}
                if pair.item_b_id not in items_cache:
                    items_cache[pair.item_b_id] = kr.get(pair.item_b_id) or {}
                item_a = items_cache[pair.item_a_id]
                item_b = items_cache[pair.item_b_id]

                prompt = JUDGE_PROMPT.format(
                    id_a=pair.item_a_id, title_a=item_a.get("title", ""),
                    created_a=item_a.get("created_at", ""),
                    content_a=(item_a.get("content") or "")[:500],
                    id_b=pair.item_b_id, title_b=item_b.get("title", ""),
                    created_b=item_b.get("created_at", ""),
                    content_b=(item_b.get("content") or "")[:500],
                )
                resp = llm.chat([{"role": "user", "content": prompt}], silent=True)
                # LLMService.chat 返回 str
                text = strip_think(resp).strip() if isinstance(resp, str) else str(resp)
                text = text.strip()
                # 去除可能的 ```json 标记
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

                data = json.loads(text)
                relation_type = data.get("relation_type", "unrelated")
                newer_item_id = data.get("newer_item_id", "")
                # newer_item_id 用 A/B 标识，转成实际 id
                if newer_item_id == "A":
                    newer_item_id = pair.item_a_id
                elif newer_item_id == "B":
                    newer_item_id = pair.item_b_id
                else:
                    newer_item_id = newer_item_id or None
                confidence = float(data.get("confidence", 0.0))
                reason = data.get("reason", "")

                self._repo.update_pair_judgment(
                    pair.id, relation_type, newer_item_id, confidence, reason
                )
                self._repo.increment_session_counter(session_id, "pairs_judged", 1)

                # unrelated 直接标记为 ignored（不写 ignore 表）
                if relation_type == "unrelated":
                    self._repo.update_pair_status(pair.id, "ignored")
                    self._repo.increment_session_counter(session_id, "pairs_ignored", 1)

                judged += 1
            except Exception as e:
                logger.warning("Judge failed for pair %s: %s", pair.id, e)
                errors.append({"pair_id": pair.id, "error": str(e)})
                # 失败的 pair 保持 pending，confidence=0
                self._repo.update_pair_judgment(
                    pair.id, "unrelated", None, 0.0,
                    f"判断失败: {e}"
                )

        return {"judged": judged, "errors": errors}

    # ── 用户操作 ──

    def list_pairs(self, session_id: str, status: str | None = None,
                   relation_type: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
        """分页查询候选对，JOIN knowledge_items 返回标题。"""
        return self._repo.list_pairs(
            session_id, status=status, relation_type=relation_type,
            limit=limit, offset=offset,
        )

    def execute_delete(self, pair_id: str, operator: str = "user") -> dict:
        """确认删除旧版本。

        1. 读 pair 的 newer_item_id，确定要删的是另一侧
        2. 调 Database.soft_delete_knowledge
        3. 写 operation_log（带快照，支持现有 undo）
        4. 更新 pair.status = 'deleted'

        Returns:
            {"ok": True, "deleted_item_id":..., "operation_log_id":...}
            或 {"ok": False, "error": {"code", "message"}}
        """
        pair = self._repo.get_pair(pair_id)
        if not pair:
            return {"ok": False, "error": {"code": "NOT_FOUND",
                                             "message": f"pair 不存在: {pair_id}"}}

        if pair.relation_type == "partial_overlap":
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": "partial_overlap 不允许直接删除，需用户手动选择删哪条",
            }}

        if pair.relation_type not in ("supersedes", "superseded_by"):
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": f"relation_type={pair.relation_type} 不支持删除",
            }}

        if not pair.newer_item_id:
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": "newer_item_id 缺失，无法确定删除哪条",
            }}

        if pair.newer_item_id not in (pair.item_a_id, pair.item_b_id):
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": f"newer_item_id={pair.newer_item_id} 不属于候选对，拒绝删除",
            }}

        # 防止重复删除：pair 已是 deleted 状态直接返回错误
        if pair.status == "deleted":
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": f"pair {pair_id} 已删除，不能重复操作",
            }}

        # 确定要删的（旧版）
        if pair.item_a_id == pair.newer_item_id:
            deleted_id = pair.item_b_id
        else:
            deleted_id = pair.item_a_id

        kr = self._get_knowledge_repo()
        item = kr.get(deleted_id, include_deleted=True)
        if not item:
            return {"ok": False, "error": {
                "code": "NOT_FOUND",
                "message": f"待删条目不存在: {deleted_id}",
            }}
        if item.get("deleted_at"):
            return {"ok": False, "error": {
                "code": "PRECONDITION_FAILED",
                "message": f"条目 {deleted_id} 已删除",
            }}

        # 写 operation_log（before 快照）
        log_id = ""
        try:
            op_service = self._get_operation_log_service()
            log_id = op_service.log(
                operation="delete",
                target_type="knowledge",
                target_id=deleted_id,
                operator=operator,
                source="version_conflict",
                before={
                    "title": item.get("title", ""),
                    "content": (item.get("content") or "")[:2000],
                    "tags": item.get("tags", "[]"),
                    "deleted_at": None,
                },
                after={"deleted_at": "set", "reason": "version_conflict_cleanup"},
                metadata={
                    "pair_id": pair_id,
                    "newer_item_id": pair.newer_item_id,
                    "relation_type": pair.relation_type,
                },
            )
        except Exception as e:
            logger.warning("Failed to write operation_log: %s", e)

        # 软删
        ok = Database.soft_delete_knowledge(deleted_id)
        if not ok:
            return {"ok": False, "error": {
                "code": "INTERNAL_ERROR",
                "message": f"软删除失败: {deleted_id}",
            }}

        # 清理 vectorstore 中该条目的向量（避免已删旧版仍被检索命中）
        # 失败不阻断主流程，仅记录日志（软删已成功，向量残留可后续清理）
        try:
            vs = self._get_vectorstore()
            vs.delete_by_knowledge(deleted_id)
        except Exception as e:
            logger.warning("Failed to clean vectorstore for %s: %s", deleted_id, e)

        # 更新 pair 状态
        self._repo.update_pair_status(pair_id, "deleted")
        self._repo.increment_session_counter(pair.session_id, "pairs_deleted", 1)

        return {
            "ok": True,
            "deleted_item_id": deleted_id,
            "operation_log_id": log_id,
            "pair_id": pair_id,
        }

    def ignore_pair(self, pair_id: str) -> dict:
        """用户判定误报。写 conflict_ignores 表 + 更新 pair.status。"""
        pair = self._repo.get_pair(pair_id)
        if not pair:
            return {"ok": False, "error": {"code": "NOT_FOUND",
                                             "message": f"pair 不存在: {pair_id}"}}
        ignore = ConflictIgnore.from_pair(
            pair.item_a_id, pair.item_b_id, source_pair_id=pair_id
        )
        self._repo.add_ignore(ignore)
        self._repo.update_pair_status(pair_id, "ignored")
        self._repo.increment_session_counter(pair.session_id, "pairs_ignored", 1)
        return {"ok": True, "pair_id": pair_id, "ignored": True}

    def list_ignores(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """列出忽略记录。"""
        return self._repo.list_ignores(limit=limit, offset=offset)

    def delete_ignore(self, ignore_id: str) -> dict:
        """撤销忽略。"""
        ok = self._repo.delete_ignore(ignore_id)
        if not ok:
            return {"ok": False, "error": {"code": "NOT_FOUND",
                                             "message": f"ignore 不存在: {ignore_id}"}}
        return {"ok": True, "deleted": True, "ignore_id": ignore_id}
