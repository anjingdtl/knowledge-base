"""Canonical Wiki v2 数据模型(Markdown canonical store + Claim 证据链)。

风格对齐 src/models/block.py:@dataclass + from_dict/to_dict。
strict=True 拒绝未知键与缺必填字段(模型层 schema 校验);
strict=False 容忍未知键(向前兼容读取老 canonical 文件)。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class PageType(str, Enum):
    SOURCES = "sources"
    ENTITIES = "entities"
    CONCEPTS = "concepts"
    COMPARISONS = "comparisons"
    SYNTHESES = "syntheses"


# C3 收敛点:权威 page_type 真源,供 wiki_index_compiler / project_setup 引用
PAGE_TYPES: tuple[str, ...] = tuple(t.value for t in PageType)


class PageStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class ClaimStatus(str, Enum):
    ACTIVE = "active"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    UNSUPPORTED = "unsupported"
    RETRACTED = "retracted"
    DRAFT = "draft"


class EvidenceStance(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"
    SUPERSEDES = "supersedes"


@dataclass
class Evidence:
    evidence_id: str
    stance: EvidenceStance
    knowledge_id: str
    block_id: str | None = None
    location: dict = field(default_factory=dict)
    source_revision: str = ""
    excerpt_hash: str | None = None
    observed_at: str = ""

    @classmethod
    def from_dict(cls, d: dict, strict: bool = True) -> "Evidence":
        if not d.get("knowledge_id"):
            raise ValueError("Evidence 必须有 knowledge_id")
        stance = d["stance"] if isinstance(d["stance"], EvidenceStance) else EvidenceStance(d["stance"])
        return cls(
            evidence_id=d["evidence_id"], stance=stance, knowledge_id=d["knowledge_id"],
            block_id=d.get("block_id"), location=d.get("location") or {},
            source_revision=d.get("source_revision", ""), excerpt_hash=d.get("excerpt_hash"),
            observed_at=d.get("observed_at", ""),
        )

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id, "stance": self.stance.value,
            "knowledge_id": self.knowledge_id, "block_id": self.block_id,
            "location": self.location, "source_revision": self.source_revision,
            "excerpt_hash": self.excerpt_hash, "observed_at": self.observed_at,
        }


@dataclass
class ClaimRelation:
    relation: str  # refines / supersedes / contradicts / ...
    target_claim_id: str

    def to_dict(self) -> dict:
        return {"relation": self.relation, "target_claim_id": self.target_claim_id}

    @classmethod
    def from_dict(cls, d: dict) -> "ClaimRelation":
        return cls(relation=d["relation"], target_claim_id=d["target_claim_id"])


@dataclass
class Claim:
    schema_version: int
    claim_id: str
    statement: str
    normalized_statement: str
    claim_type: str
    status: ClaimStatus
    confidence: float
    valid_from: str | None
    valid_to: str | None
    subject_refs: list[str]
    predicate: str
    object_refs: list[str]
    evidence: list[Evidence]
    relations: list[ClaimRelation]
    created_at: str
    updated_at: str
    revision: int

    def validate(self) -> list[str]:
        """跨字段 invariant 校验,返回错误描述列表(空=合法)。"""
        errors: list[str] = []
        if self.revision < 1:
            errors.append("revision 必须是正整数")
        if self.status is ClaimStatus.ACTIVE:
            supports = [e for e in self.evidence if e.stance is EvidenceStance.SUPPORTS]
            if not supports:
                errors.append("active Claim 必须至少有一条 supports Evidence")
        return errors

    @classmethod
    def from_dict(cls, d: dict, strict: bool = True) -> "Claim":
        required = ["schema_version", "claim_id", "statement", "normalized_statement",
                    "claim_type", "status", "confidence", "subject_refs", "predicate",
                    "object_refs", "evidence", "created_at", "updated_at", "revision"]
        known = set(required) | {"valid_from", "valid_to", "relations"}
        if strict:
            extra = set(d.keys()) - known
            if extra:
                raise ValueError(f"Claim 未知字段(strict): {sorted(extra)}")
            for k in required:
                if k not in d:
                    raise ValueError(f"Claim 缺必填字段: {k}")
        rev = int(d["revision"])
        if rev < 1:
            raise ValueError("revision 必须是正整数")
        status = d["status"] if isinstance(d["status"], ClaimStatus) else ClaimStatus(d["status"])
        return cls(
            schema_version=int(d["schema_version"]), claim_id=d["claim_id"],
            statement=d["statement"], normalized_statement=d["normalized_statement"],
            claim_type=d["claim_type"], status=status, confidence=float(d["confidence"]),
            valid_from=d.get("valid_from"), valid_to=d.get("valid_to"),
            subject_refs=list(d.get("subject_refs", [])), predicate=d.get("predicate", ""),
            object_refs=list(d.get("object_refs", [])),
            evidence=[Evidence.from_dict(e, strict=strict) for e in d.get("evidence", [])],
            relations=[ClaimRelation.from_dict(r) for r in d.get("relations", [])],
            created_at=d["created_at"], updated_at=d["updated_at"], revision=rev,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "claim_id": self.claim_id,
            "statement": self.statement, "normalized_statement": self.normalized_statement,
            "claim_type": self.claim_type, "status": self.status.value,
            "confidence": self.confidence, "valid_from": self.valid_from, "valid_to": self.valid_to,
            "subject_refs": list(self.subject_refs), "predicate": self.predicate,
            "object_refs": list(self.object_refs),
            "evidence": [e.to_dict() for e in self.evidence],
            "relations": [r.to_dict() for r in self.relations],
            "created_at": self.created_at, "updated_at": self.updated_at, "revision": self.revision,
        }


@dataclass
class WikiPage:
    schema_version: int
    page_id: str
    title: str
    page_type: PageType
    status: PageStatus
    revision: int
    aliases: list[str]
    tags: list[str]
    source_ids: list[str]
    claim_ids: list[str]
    created_at: str
    updated_at: str
    content_hash: str
    body: str
    supersedes_page_id: str | None = None

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.revision < 1:
            errors.append("revision 必须是正整数")
        if self.status is PageStatus.PUBLISHED:
            # published 页面不得引用 draft Claim(精确校验在 WikiValidator,此处只防 page 自身 draft claim_ids 命名约定缺失)
            pass
        return errors

    @classmethod
    def from_dict(cls, d: dict, strict: bool = True) -> "WikiPage":
        required = ["schema_version", "page_id", "title", "page_type", "status",
                    "revision", "source_ids", "claim_ids", "created_at", "updated_at",
                    "content_hash", "body"]
        known = set(required) | {"aliases", "tags", "supersedes_page_id"}
        if strict:
            extra = set(d.keys()) - known
            if extra:
                raise ValueError(f"WikiPage 未知字段(strict): {sorted(extra)}")
            for k in required:
                if k not in d:
                    raise ValueError(f"WikiPage 缺必填字段: {k}")
        rev = int(d["revision"])
        if rev < 1:
            raise ValueError("revision 必须是正整数")
        pt = d["page_type"] if isinstance(d["page_type"], PageType) else PageType(d["page_type"])
        st = d["status"] if isinstance(d["status"], PageStatus) else PageStatus(d["status"])
        return cls(
            schema_version=int(d["schema_version"]), page_id=d["page_id"], title=d["title"],
            page_type=pt, status=st, revision=rev, aliases=list(d.get("aliases", [])),
            tags=list(d.get("tags", [])), source_ids=list(d.get("source_ids", [])),
            claim_ids=list(d.get("claim_ids", [])), created_at=d["created_at"],
            updated_at=d["updated_at"], content_hash=d["content_hash"], body=d.get("body", ""),
            supersedes_page_id=d.get("supersedes_page_id"),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "page_id": self.page_id, "title": self.title,
            "page_type": self.page_type.value, "status": self.status.value, "revision": self.revision,
            "aliases": list(self.aliases), "tags": list(self.tags), "source_ids": list(self.source_ids),
            "claim_ids": list(self.claim_ids), "created_at": self.created_at, "updated_at": self.updated_at,
            "content_hash": self.content_hash, "body": self.body,
            "supersedes_page_id": self.supersedes_page_id,
        }


@dataclass
class PageRegistryEntry:
    path: str
    title: str
    page_type: str
    revision: int
    content_hash: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PageRegistryEntry":
        return cls(path=d["path"], title=d["title"], page_type=d["page_type"],
                   revision=int(d["revision"]), content_hash=d["content_hash"])


@dataclass
class SaveResult:
    ok: bool
    object_id: str
    revision: int
    warnings: list[str] = field(default_factory=list)
    outbox_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationFinding:
    path: str
    object_id: str
    category: str  # schema_invalid / claim_missing / evidence_missing / evidence_stale / projection_drift / registry_drift / publish_gate_violation
    severity: str  # error / warning
    message: str
    detail: dict = field(default_factory=dict)
