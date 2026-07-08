"""Claim Matcher — 跨来源 Claim 匹配分类器。

给定新 Claim + 候选已存在 Claim 列表，判定合并动作（merge action）。
输出 ClaimMatchDecision(action, target_claim_id, score, reasons, reason_codes)。

决策逻辑纯函数式（embedding+规则），确定性，不调 LLM。

C1 契约: ClaimMergeAction / ReasonCode 枚举 + normalize 共用见
docs/architecture/wiki-v2-claim-merge-contract.md。
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.models.wiki_v2 import Claim, EvidenceStance, normalize_statement

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# C1 契约枚举(唯一 action / reason code 真源,Matcher 与 MergeEngine 共用)
# ---------------------------------------------------------------------------
class ClaimMergeAction(str, Enum):
    """Claim 合并动作(C1 契约 §1)。Matcher 产出,MergeEngine 消费。"""

    NEW = "new"
    SUPPORTS = "supports"
    REFINES = "refines"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    DUPLICATE = "duplicate"
    UNRESOLVED = "unresolved"


class ReasonCode(str, Enum):
    """稳定 reason code(C1 契约 §2),机器可读;reasons 是人类可读自然语言。"""

    NO_CANDIDATES = "no_candidates"
    EXACT_NORMALIZED_MATCH = "exact_normalized_match"
    OBJECT_REFS_CONFLICT = "object_refs_conflict"
    LOW_CONFIDENCE = "low_confidence"
    AMBIGUOUS_CANDIDATES = "ambiguous_candidates"
    TEMPORAL_SUPERSEDES = "temporal_supersedes"
    NEW_HAS_CONTRADICTS_EVIDENCE = "new_has_contradicts_evidence"
    REFINES_SUPERSET = "refines_superset"
    SUPPORTS_FALLBACK = "supports_fallback"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------
@dataclass
class ClaimMatchDecision:
    """Matcher 的输出：合并动作决策。

    reasons: 人类可读自然语言(兼容已有测试)。
    reason_codes: 稳定机器可读 code(C1,见 ReasonCode 枚举)。
    """

    action: str  # ClaimMergeAction.*.value
    target_claim_id: str | None = None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ClaimMatcher
# ---------------------------------------------------------------------------
class ClaimMatcher:
    """跨来源 Claim 匹配分类器。

    对每个候选 Claim 计算相似度，按决策树（C1 契约 §5）判定合并动作。
    相似度可通过 scores 参数注入（测试用），或通过 embedding 服务计算。
    """

    def __init__(self, embedding: Any = None, config: Any = None) -> None:
        self._embedding = embedding
        self._config = config

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读取配置，优先注入的 config。"""
        if self._config is not None:
            return self._config.get(key, default)
        return default

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def match(
        self,
        new_claim: Claim,
        candidates: list[Claim],
        scores: dict[str, float] | None = None,
    ) -> ClaimMatchDecision:
        """判定 new_claim 相对 candidates 的合并动作。"""
        unresolved_thresh = self._cfg("wiki.claims.unresolved_threshold", 0.72)
        semantic_thresh = self._cfg("wiki.claims.semantic_match_threshold", 0.88)

        # Step 1: No candidates → new
        if not candidates:
            return ClaimMatchDecision(
                action=ClaimMergeAction.NEW.value, target_claim_id=None, score=0.0,
                reasons=["no candidates provided"],
                reason_codes=[ReasonCode.NO_CANDIDATES.value],
            )

        # Step 2: exact hash match (deterministic truth, independent of scores)
        new_hash = self._exact_hash(new_claim)
        exact_match: Claim | None = None
        for c in candidates:
            if self._exact_hash(c) == new_hash:
                exact_match = c
                break

        if exact_match is not None:
            reasons_exact = ["exact normalized_statement match (sha256)"]
            codes_exact = [ReasonCode.EXACT_NORMALIZED_MATCH.value]
            # Exact match with different object_refs → contradicts (decision tree 2a).
            if (
                exact_match.object_refs
                and new_claim.object_refs
                and set(new_claim.object_refs) != set(exact_match.object_refs)
            ):
                reasons_exact.append(
                    f"object_refs conflict: {set(new_claim.object_refs)} vs {set(exact_match.object_refs)}"
                )
                codes_exact.append(ReasonCode.OBJECT_REFS_CONFLICT.value)
                return ClaimMatchDecision(
                    action=ClaimMergeAction.CONTRADICTS.value,
                    target_claim_id=exact_match.claim_id, score=1.0,
                    reasons=reasons_exact, reason_codes=codes_exact,
                )
            return ClaimMatchDecision(
                action=ClaimMergeAction.DUPLICATE.value,
                target_claim_id=exact_match.claim_id, score=1.0,
                reasons=reasons_exact, reason_codes=codes_exact,
            )

        # Step 3: Compute or use injected scores
        if scores is None:
            scores = self._embed_scores(new_claim, candidates)

        # Find best candidate by score
        best_claim: Claim | None = None
        best_score: float = 0.0
        for c in candidates:
            s = scores.get(c.claim_id, 0.0)
            if s > best_score:
                best_score = s
                best_claim = c

        if best_claim is None or best_score < unresolved_thresh:
            return ClaimMatchDecision(
                action=ClaimMergeAction.NEW.value, target_claim_id=None,
                score=best_score,
                reasons=[f"best score {best_score:.2f} < unresolved threshold {unresolved_thresh}"],
                reason_codes=[ReasonCode.LOW_CONFIDENCE.value],
            )

        # --- From here, best_claim is not None and best_score >= unresolved_thresh ---

        # Step 4: semantic mid-range (unresolved_threshold <= score < semantic_threshold)
        if unresolved_thresh <= best_score < semantic_thresh:
            return ClaimMatchDecision(
                action=ClaimMergeAction.UNRESOLVED.value,
                target_claim_id=best_claim.claim_id, score=best_score,
                reasons=[
                    f"semantic similarity {best_score:.2f} in unresolved range "
                    f"[{unresolved_thresh}, {semantic_thresh})",
                ],
                reason_codes=[ReasonCode.AMBIGUOUS_CANDIDATES.value],
            )

        # Step 5: high semantic (best_score >= semantic_threshold, non-exact)
        target = best_claim
        reasons_high = [f"semantic similarity {best_score:.2f} >= {semantic_thresh}"]
        codes_high: list[str] = []

        # 5a: supersedes (temporal update takes priority over plain object conflict)
        supersedes_field = self._supersedes(new_claim, target)
        if supersedes_field:
            target_time_value = target.valid_to if supersedes_field == "valid_to" else target.valid_from
            reasons_high.append(
                f"temporal supersedes: new valid_from={new_claim.valid_from} "
                f"> target {supersedes_field}={target_time_value}"
            )
            codes_high.append(ReasonCode.TEMPORAL_SUPERSEDES.value)
            return ClaimMatchDecision(
                action=ClaimMergeAction.SUPERSEDES.value,
                target_claim_id=target.claim_id, score=best_score,
                reasons=reasons_high, reason_codes=codes_high,
            )

        # 5b: objects conflict OR new has contradicts stance evidence → contradicts
        if self._objects_conflict(new_claim, target):
            reasons_high.append(
                f"object_refs conflict: {set(new_claim.object_refs)} vs {set(target.object_refs)}"
            )
            codes_high.append(ReasonCode.OBJECT_REFS_CONFLICT.value)
            return ClaimMatchDecision(
                action=ClaimMergeAction.CONTRADICTS.value,
                target_claim_id=target.claim_id, score=best_score,
                reasons=reasons_high, reason_codes=codes_high,
            )

        if self._has_contradicts_evidence(new_claim):
            reasons_high.append("new claim has contradicts-stance evidence")
            codes_high.append(ReasonCode.NEW_HAS_CONTRADICTS_EVIDENCE.value)
            return ClaimMatchDecision(
                action=ClaimMergeAction.CONTRADICTS.value,
                target_claim_id=target.claim_id, score=best_score,
                reasons=reasons_high, reason_codes=codes_high,
            )

        # 5c: refines
        if self._refines(new_claim, target):
            reasons_high.append(
                f"new claim refines target: "
                f"subject_refs {set(new_claim.subject_refs)} ⊋ {set(target.subject_refs)} "
                f"or object_refs {set(new_claim.object_refs)} ⊋ {set(target.object_refs)}"
            )
            codes_high.append(ReasonCode.REFINES_SUPERSET.value)
            return ClaimMatchDecision(
                action=ClaimMergeAction.REFINES.value,
                target_claim_id=target.claim_id, score=best_score,
                reasons=reasons_high, reason_codes=codes_high,
            )

        # 5d: supports (fallback)
        reasons_high.append("no conflict, supersedes, or refinement detected")
        codes_high.append(ReasonCode.SUPPORTS_FALLBACK.value)
        return ClaimMatchDecision(
            action=ClaimMergeAction.SUPPORTS.value,
            target_claim_id=target.claim_id, score=best_score,
            reasons=reasons_high, reason_codes=codes_high,
        )

    # ------------------------------------------------------------------
    # Private: hashing & cosine
    # ------------------------------------------------------------------
    def _exact_hash(self, claim: Claim) -> str:
        """sha256(normalized_statement)。归一化委托 models.normalize_statement(C1)。"""
        normalized = claim.normalized_statement or normalize_statement(claim.statement)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """纯 Python cosine similarity（禁 numpy）。"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _embed_scores(self, new_claim: Claim, candidates: list[Claim]) -> dict[str, float]:
        """Embedding 路径：用 self._embedding 计算 cosine similarity。

        无 embedding → 所有 score 为 0.0（退化为纯 exact+lexical）。
        """
        scores: dict[str, float] = {}
        if self._embedding is None:
            for c in candidates:
                scores[c.claim_id] = 0.0
            return scores

        try:
            new_vec = self._embedding.embed(new_claim.normalized_statement or new_claim.statement)
        except Exception:  # noqa: BLE001
            logger.warning("embedding failed for new claim, scoring all candidates 0.0")
            for c in candidates:
                scores[c.claim_id] = 0.0
            return scores

        for c in candidates:
            try:
                cand_vec = self._embedding.embed(c.normalized_statement or c.statement)
                scores[c.claim_id] = self._cosine(new_vec, cand_vec)
            except Exception:  # noqa: BLE001
                scores[c.claim_id] = 0.0

        return scores

    # ------------------------------------------------------------------
    # Private: auxiliary decision functions (C1 契约 §5)
    # ------------------------------------------------------------------
    @staticmethod
    def _objects_conflict(new: Claim, target: Claim) -> bool:
        """object_refs 集合不同(数值/实体差异),且 subject_refs+predicate 相同(同一主谓)。

        双方都有 object_refs 才算冲突（空 object 不触发冲突）。
        """
        if not target.object_refs or not new.object_refs:
            return False
        return (
            bool(set(new.subject_refs) & set(target.subject_refs))
            and new.predicate == target.predicate
            and set(new.object_refs) != set(target.object_refs)
        )

    @staticmethod
    def _supersedes(new: Claim, target: Claim) -> bool | str:
        """new 时间更晚 + subject+predicate 相同 + object 不同。

        Returns:
            False if not supersedes; a human-readable reason string if supersedes
            (e.g. "valid_from" or "valid_to") indicating which target field was compared.
        """
        if not new.valid_from:
            return False

        same_subject = bool(set(new.subject_refs) & set(target.subject_refs))
        same_predicate = new.predicate == target.predicate
        different_object = set(new.object_refs) != set(target.object_refs)

        if not (same_subject and same_predicate and different_object):
            return False

        # Compare dates as strings (ISO format is lexicographically comparable)
        if target.valid_to and new.valid_from > target.valid_to:
            return "valid_to"
        if target.valid_from and new.valid_from > target.valid_from:
            return "valid_from"

        return False

    @staticmethod
    def _refines(new: Claim, target: Claim) -> bool:
        """new 是 target 的细化：new.subject_refs 是 target 的真超集或 new.object_refs 是真超集。

        predicate 必须相同。
        """
        if new.predicate != target.predicate:
            return False

        new_subs = set(new.subject_refs)
        tgt_subs = set(target.subject_refs)
        if tgt_subs and new_subs > tgt_subs:
            return True

        new_objs = set(new.object_refs)
        tgt_objs = set(target.object_refs)
        if tgt_objs and new_objs > tgt_objs:
            return True

        return False

    @staticmethod
    def _has_contradicts_evidence(claim: Claim) -> bool:
        """检查 claim 是否有 contradicts stance 的 evidence。"""
        return any(e.stance == EvidenceStance.CONTRADICTS for e in claim.evidence)
