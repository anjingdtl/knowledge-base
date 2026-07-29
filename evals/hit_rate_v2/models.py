"""Shared models for hit-rate V2 scoring and Golden V2 datasets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Split = Literal["development", "regression", "validation", "holdout"]
Answerability = Literal["answerable", "no_answer", "clarification_required"]
RiskLevel = Literal["P0", "P1", "P2", "P3"]
SourceRole = Literal["primary", "supporting", "acceptable", "forbidden"]
MatchPolicy = Literal["exact", "normalized", "numeric_unit", "semantic_review"]
AmbiguityStatus = Literal["clear", "needs_clarification", "disputed"]
AnnotationSource = Literal["candidate", "human_reviewed"]


@dataclass
class CaseScore:
    """Per-case score payload (V1-compatible fields + V2 contract fields)."""

    case_id: str
    case_type: str  # answerable | no_answer
    metric_contract_version: str = "2.0"

    # Answerable metrics
    retrieval_top1_hit: bool | None = None
    retrieval_recall_at_5: bool | None = None
    answer_fact_coverage: bool | None = None
    answer_forbidden_assertion: bool | None = None
    citation_lineage_valid: bool | None = None
    answer_supported: bool | None = None
    e2e_pass: bool | None = None

    # Legacy aliases kept for CLI/report compatibility
    top1_hit: bool | None = None
    recall5: bool | None = None
    ask_fact_correct: bool | None = None
    ask_citation_valid: bool | None = None
    citation_valid: bool | None = None
    facts_correct: bool | None = None
    grounded: bool | None = None
    no_hallucination: bool | None = None
    forbidden_assertion: bool | None = None

    # No-answer metrics
    false_positive: bool | None = None
    expressed_insufficient: bool | None = None
    no_fabrication: bool | None = None
    reason_codes: list[str] = field(default_factory=list)

    # Diagnostics
    score: int = 0
    top1_id: str | None = None
    cand_ids: list[str] = field(default_factory=list)
    cand_count: int = 0
    ask_has_answer: bool = False
    ask_source_count: int = 0
    answer_mode: str | None = None
    citation_buckets: dict[str, int] = field(default_factory=dict)
    citation_valid_num: int = 0
    citation_valid_den: int = 0
    defect_severity: str | None = None
    defect_category: str | None = None
    defect_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        extra = d.pop("extra", {}) or {}
        # Flatten diagnostics commonly expected by finalize CLI
        for k, v in extra.items():
            d.setdefault(k, v)
        # Compatibility: type field used by existing reports
        d["type"] = d.pop("case_type")
        return d


@dataclass
class AggregateMetrics:
    metric_contract_version: str = "2.0"
    answerable_total: int = 0
    no_answer_total: int = 0
    top1_correct: int = 0
    recall5_correct: int = 0
    ask_fact_correct_count: int = 0
    ask_citation_valid_count: int = 0
    e2e_pass_count: int = 0
    grounded_count: int = 0
    forbidden_assertion_count: int = 0
    false_positive_count: int = 0
    citation_valid: int = 0
    citation_total: int = 0
    citation_buckets: dict[str, int] = field(default_factory=dict)

    # Rates
    top1_accuracy: float = 0.0
    recall_at_5: float = 0.0
    ask_fact_correctness: float = 0.0
    ask_citation_validity: float = 0.0
    e2e_pass_rate: float = 0.0
    answer_groundedness: float = 0.0
    citation_validity: float = 0.0
    forbidden_assertion_rate: float | None = 0.0
    false_positive_rate: float = 0.0
    # Full hallucination is not fully measurable from forbidden substrings alone
    hallucination_rate: float | None = None
    hallucination_status: str = "not_fully_measurable"

    def to_report_dict(self) -> dict[str, Any]:
        """Legacy-compatible headline keys plus V2 explicit names."""
        return {
            "metric_contract_version": self.metric_contract_version,
            "answerable_total": self.answerable_total,
            "no_answer_total": self.no_answer_total,
            "top1_correct": self.top1_correct,
            "recall5_correct": self.recall5_correct,
            "ask_fact_correct_count": self.ask_fact_correct_count,
            "ask_citation_valid_count": self.ask_citation_valid_count,
            "e2e_pass_count": self.e2e_pass_count,
            "grounded_count": self.grounded_count,
            "Top-1 Accuracy": self.top1_accuracy,
            "Recall@5": self.recall_at_5,
            "Ask Fact Correctness": self.ask_fact_correctness,
            "Ask Citation Validity": self.ask_citation_validity,
            "E2E Pass Rate": self.e2e_pass_rate,
            "Answer Groundedness": self.answer_groundedness,
            "Citation Validity": self.citation_validity,
            "citation_total": self.citation_total,
            "citation_valid": self.citation_valid,
            "citation_buckets": self.citation_buckets,
            # V2: explicit forbidden-assertion proxy (legacy field retained as alias)
            "Forbidden Assertion Rate": self.forbidden_assertion_rate,
            "Hallucination Rate": self.hallucination_rate,
            "Hallucination Status": self.hallucination_status,
            # Keep legacy name pointing to forbidden proxy for compare scripts,
            # but finalize report should prefer Forbidden Assertion Rate.
            "legacy_Hallucination_Rate_is_Forbidden_Assertion_Rate": True,
            "False Positive Rate": self.false_positive_rate,
            "false_positive_count": self.false_positive_count,
            "forbidden_assertion_count": self.forbidden_assertion_count,
        }
