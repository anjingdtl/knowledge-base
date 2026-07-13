"""Claim Extractor — 从来源 blocks 抽取带 Evidence 的 Claim。

给定 ExtractionBlock 列表，规则切句 → 候选去重 → LLM 抽取 → 构造 Claim/Evidence。
LLM 失败降级为 warnings/errors（不阻断 raw 索引）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field

from src.models.wiki_v2 import Claim, ClaimStatus, Evidence, EvidenceStance, normalize_statement
from src.services.wiki_repository import new_claim_id

logger = logging.getLogger(__name__)


def compute_excerpt_hash(text: str) -> str:
    """块内容指纹(sha256: 前缀 + hex)。

    canary/primary/shadow 的 _hash_text 与 Phase 5 rebuild block-diff 共用此函数,
    保证 evidence.excerpt_hash 与当前 block 哈希可比对(同算法)。
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# LLM prompt 常量
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "你是事实抽取器，从技术文档片段抽取**可验证的声明性事实**，"
    "每条必须能定位到来源 block。不要抽取主观评价/未来预测/无来源内容。"
)

_USER_TEMPLATE = """背景: {source_summary}

以下是从文档中提取的片段，编号后标注来源 block_id：

{fragments}

请从上述片段中抽取可验证的声明性事实。
输出格式：只输出 JSON，不要输出其他内容。
schema:
{{"claims": [{{"statement": "str", "claim_type": "fact|definition|relation", "confidence": 0.0-1.0, "evidence_block_id": "str", "stance": "supports|contradicts", "subject_refs": ["str"], "predicate": "str", "object_refs": ["str"]}}]}}

evidence_block_id 必须是给定的某个 block_id。"""


@dataclass
class ExtractionBlock:
    """轻量结构，解耦 Block 模型。Phase 4 ingest 流程负责 Block → ExtractionBlock。"""

    block_id: str
    content: str
    location: dict
    source_revision: str
    excerpt_hash: str | None = None


@dataclass
class ClaimExtractionResult:
    """extract() 的返回值。"""

    extracted_claims: list = field(default_factory=list)  # list[Claim]
    skipped_fragments: list = field(default_factory=list)  # list[str]
    llm_calls: int = 0
    warnings: list = field(default_factory=list)  # list[str]
    errors: list = field(default_factory=list)  # list[str]


class ClaimExtractor:
    """从来源 blocks 抽取带 Evidence 的 Claim。"""

    def __init__(self, llm, config=None) -> None:
        """初始化抽取器。

        Args:
            llm: LLMService 实例（src/services/llm.py）。
            config: Config 或 dict-like（有 .get 方法）。
        """
        self._llm = llm
        self._config = config

    def _cfg(self, key: str, default=None):
        """读取配置，优先注入的 config，回退到默认值。"""
        if self._config is not None:
            return self._config.get(key, default)
        return default

    def extract(
        self,
        knowledge_id: str,
        blocks: list,
        source_summary: str,
        candidate_claims: list | None = None,
        now: str = "",
        max_claims: int | None = None,
        max_llm_calls: int | None = None,
    ) -> ClaimExtractionResult:
        """规则切句 → 筛除候选 → LLM 抽取 → 构造 Claim+Evidence。

        LLM 失败降级为 warnings（不抛）。
        返回的 Claim: status=ClaimStatus.DRAFT, confidence 来自 LLM。
        """
        result = ClaimExtractionResult()

        enabled = self._cfg("wiki.claims.enabled", True)
        if not enabled:
            return result

        _max_claims = max_claims if max_claims is not None else self._cfg("wiki.claims.max_claims_per_ingest", 30)
        _max_llm_calls = max_llm_calls if max_llm_calls is not None else self._cfg("wiki.claims.max_llm_calls_per_ingest", 4)
        _require_block_evidence = self._cfg("wiki.claims.require_block_evidence", True)

        # 1. 规则切句
        fragments = self._split_fragments(blocks, result)

        if not fragments:
            return result

        # 2. 候选去重
        if candidate_claims:
            fragments = self._dedupe_against_candidates(fragments, candidate_claims, result)

        if not fragments:
            return result

        # 3. fragment 归一化去重（防重复送 LLM）
        fragments = self._dedupe_fragments(fragments, result)

        if not fragments:
            return result

        # 4. LLM 抽取（分批）
        call_counter: list[int] = [0]
        try:
            raw_claims = self._call_llm(
                fragments, source_summary, _max_llm_calls, call_counter
            )
            result.llm_calls = call_counter[0]
        except Exception as exc:  # noqa: BLE001
            # spec §8.1 铁律：extractor 是 ingest 一环，任何 LLM 失败（异常/超时/坏 JSON）
            # 降级为 warnings/errors 返回空结果，绝不向上抛，不阻断 raw 索引。
            result.llm_calls = call_counter[0]
            result.errors.append(f"LLM 抽取失败: {exc}")
            return result

        # 5. 构造 Claim + Evidence
        block_ids = {b.block_id for b in blocks}
        seen_normalized: set[str] = set()
        for raw in raw_claims:
            if len(result.extracted_claims) >= _max_claims:
                result.warnings.append(f"达到 max_claims={_max_claims}，截断剩余 claim")
                break

            statement = raw.get("statement", "")
            if not statement:
                continue

            normalized = self._normalize(statement)
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)

            evidence_block_id = raw.get("evidence_block_id", "")
            if _require_block_evidence and evidence_block_id not in block_ids:
                result.warnings.append(
                    f"claim '{statement[:50]}' 的 evidence_block_id={evidence_block_id} "
                    f"不在给定 blocks 中，丢弃"
                )
                continue

            # 找到对应的 fragment（用于 block 元数据）
            frag = next(
                (f for f in fragments if f["block_id"] == evidence_block_id),
                fragments[0],
            )

            claim = self._build_claim(raw, frag, knowledge_id, now)
            result.extracted_claims.append(claim)

        return result

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _split_fragments(self, blocks: list, result: ClaimExtractionResult) -> list[dict]:
        """规则切句: 按 。/./换行 切分，过滤无信息量碎片。

        返回 list[dict]，每个 dict 含 text, block_id, location, source_revision, excerpt_hash。
        """
        fragments: list[dict] = []
        for block in blocks:
            content = block.content
            # 按 。  /  .  / 换行切分
            parts = re.split(r"(?<=[。.！!？?\n])", content)
            for part in parts:
                text = part.strip()
                if not text:
                    continue
                # 过滤无信息量碎片: 纯标题 / len < 8 / 纯标点
                cleaned = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
                if len(cleaned) < 8:
                    result.skipped_fragments.append(text)
                    continue
                fragments.append(
                    {
                        "text": text,
                        "block_id": block.block_id,
                        "location": block.location,
                        "source_revision": block.source_revision,
                        "excerpt_hash": block.excerpt_hash,
                    }
                )
        return fragments

    def _dedupe_against_candidates(
        self, fragments: list[dict], candidates: list, result: ClaimExtractionResult
    ) -> list[dict]:
        """对每个 fragment 归一化后与候选 claim 的 normalized_statement 比较，相同则跳过。"""
        candidate_norms = set()
        for c in candidates:
            if hasattr(c, "normalized_statement") and c.normalized_statement:
                candidate_norms.add(c.normalized_statement)
            elif hasattr(c, "statement") and c.statement:
                candidate_norms.add(self._normalize(c.statement))

        kept: list[dict] = []
        for frag in fragments:
            norm = self._normalize(frag["text"])
            if norm in candidate_norms:
                result.skipped_fragments.append(frag["text"])
            else:
                kept.append(frag)
        return kept

    def _dedupe_fragments(self, fragments: list[dict], result: ClaimExtractionResult) -> list[dict]:
        """对 fragments 按归一化文本去重，相同 normalized 只保留第一条。"""
        seen: set[str] = set()
        kept: list[dict] = []
        for frag in fragments:
            norm = self._normalize(frag["text"])
            if norm in seen:
                result.skipped_fragments.append(frag["text"])
            else:
                seen.add(norm)
                kept.append(frag)
        return kept

    def _normalize(self, text: str) -> str:
        """归一化(委托 models.normalize_statement,C1 契约:禁止各自重造)。"""
        return normalize_statement(text)

    def _call_llm(
        self, fragments: list[dict], source_summary: str, max_llm_calls: int, call_counter: list[int]
    ) -> list[dict]:
        """组装 prompt，调 llm.chat()，解析 JSON。返回 raw_claims_list。

        call_counter: mutable [int] 传入，每次 LLM 调用后 +1（即使后续解析失败，调用次数也已记录）。
        fragments 分批，每批一次 LLM。超 max_llm_calls 停。
        LLM 异常或 JSON 解析失败 → 抛 RuntimeError/ValueError（由 extract 捕获降级）。
        """
        batch_size = max(1, (len(fragments) + max_llm_calls - 1) // max_llm_calls)
        all_raw_claims: list[dict] = []

        for i in range(0, len(fragments), batch_size):
            if call_counter[0] >= max_llm_calls:
                break
            batch = fragments[i : i + batch_size]
            frag_text = "\n".join(
                f"[{j}] {f['text']} (block: {f['block_id']})"
                for j, f in enumerate(batch)
            )
            user_msg = _USER_TEMPLATE.format(
                source_summary=source_summary, fragments=frag_text
            )
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ]

            try:
                response = self._llm.chat(messages, silent=True, max_tokens_override=2048)
            except RuntimeError as exc:
                # 记录已完成的 LLM 调用次数，方便 extract 层回填
                raise RuntimeError(f"LLM 调用异常: {exc}") from exc

            call_counter[0] += 1

            # 解析 JSON 容错
            parsed = self._parse_llm_json(response)
            if parsed is None:
                raise ValueError(f"LLM 返回内容无法解析为 JSON: {response[:200]}")
            if "claims" in parsed:
                all_raw_claims.extend(parsed["claims"])

        return all_raw_claims

    @staticmethod
    def _parse_llm_json(text: str) -> dict | None:
        """解析 LLM 返回的 JSON，容错处理 ```json``` 围栏。

        解析失败返回 None（由调用方决定降级策略）。
        """
        text = text.strip()
        # 剥 ```json 围栏
        m = re.match(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
        try:
            result: dict | None = json.loads(text)
            if isinstance(result, dict):
                return result
            return None
        except (json.JSONDecodeError, ValueError):
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                result = json.loads(text[start : end + 1])
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                return None
            return None

    def _build_claim(self, raw: dict, frag: dict, knowledge_id: str, now: str) -> Claim:
        """raw(LLM 输出) + frag(block 元数据) → Claim + Evidence。"""
        evidence_id = f"ev_{uuid.uuid4().hex[:12]}"

        stance_val = raw.get("stance", "supports")
        try:
            stance = EvidenceStance(stance_val)
        except ValueError:
            stance = EvidenceStance.SUPPORTS

        evidence = Evidence(
            evidence_id=evidence_id,
            stance=stance,
            knowledge_id=knowledge_id,
            block_id=frag["block_id"],
            location=frag["location"],
            source_revision=frag["source_revision"],
            excerpt_hash=frag.get("excerpt_hash"),
            observed_at=now,
        )

        claim = Claim(
            schema_version=1,
            claim_id=new_claim_id(),
            statement=raw["statement"],
            normalized_statement=self._normalize(raw["statement"]),
            claim_type=raw.get("claim_type", "fact"),
            status=ClaimStatus.DRAFT,
            confidence=float(raw.get("confidence", 0.5)),
            valid_from=None,
            valid_to=None,
            subject_refs=raw.get("subject_refs", []),
            predicate=raw.get("predicate", ""),
            object_refs=raw.get("object_refs", []),
            evidence=[evidence],
            relations=[],
            created_at=now,
            updated_at=now,
            revision=1,
        )
        return claim
