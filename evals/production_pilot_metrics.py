"""Production-pilot metrics with strict denominators (no default full scores).

Deprecated: scripts/final_closure_mcp_harness.py golden scoring paths that
award 1.0 when expected_ids is empty.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class MetricValue:
    numerator: int | float
    denominator: int
    excluded: int = 0

    @property
    def value(self) -> float | None:
        if self.denominator <= 0:
            return None
        return float(self.numerator) / float(self.denominator)

    def as_dict(self) -> dict[str, Any]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "excluded": self.excluded,
            "value": self.value,
        }


def _rel_set(expected: Iterable[str], acceptable: Iterable[str] | None = None) -> set[str]:
    s = set(expected or [])
    if acceptable:
        s |= set(acceptable)
    return s


def _dcg(rels: list[float]) -> float:
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(rels))


def score_retrieval(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Recall@1/5, MRR@10, nDCG@10, Precision@5, Forbidden Hit Rate@5."""
    r1_n = r1_d = 0
    r5_n = r5_d = 0
    mrr_sum = 0.0
    mrr_d = 0
    ndcg_sum = 0.0
    ndcg_d = 0
    p5_sum = 0.0
    p5_d = 0
    forb_n = forb_d = 0
    excluded_empty = 0

    for row in rows:
        expected = list(row.get("expected_ids") or [])
        acceptable = list(row.get("acceptable_ids") or [])
        forbidden = set(row.get("forbidden_ids") or [])
        got = list(row.get("got_ids") or [])
        rel = _rel_set(expected, acceptable)

        if not rel:
            excluded_empty += 1
            continue

        top1 = got[:1]
        top5 = got[:5]
        top10 = got[:10]

        r1_d += 1
        if set(top1) & rel:
            r1_n += 1

        r5_d += 1
        if set(top5) & rel:
            r5_n += 1

        mrr_d += 1
        rr = 0.0
        for i, gid in enumerate(top10, start=1):
            if gid in rel:
                rr = 1.0 / i
                break
        mrr_sum += rr

        # Graded relevance: expected=2, acceptable=1 (nDCG@10, clamped to [0,1])
        exp_set = set(expected)
        acc_set = set(acceptable)
        grades = []
        for gid in top10:
            if gid in exp_set:
                grades.append(2.0)
            elif gid in acc_set:
                grades.append(1.0)
            else:
                grades.append(0.0)
        ideal_grades = [2.0] * min(len(exp_set), 10) + [1.0] * min(
            len(acc_set - exp_set), max(0, 10 - min(len(exp_set), 10))
        )
        ideal_grades = sorted(ideal_grades, reverse=True)[:10]
        # Pad ideal to same length as grades for fair DCG comparison
        if len(ideal_grades) < len(grades):
            ideal_grades = ideal_grades + [0.0] * (len(grades) - len(ideal_grades))
        idcg = _dcg(ideal_grades)
        dcg = _dcg(grades)
        ndcg_d += 1
        if idcg > 0:
            ndcg_sum += min(1.0, max(0.0, dcg / idcg))
        else:
            ndcg_sum += 0.0

        # Precision@5: fraction of top-5 that are relevant (expected∪acceptable)
        p5_d += 1
        if top5:
            p5_sum += len(set(top5) & rel) / 5.0
        else:
            p5_sum += 0.0

        if forbidden:
            forb_d += 1
            if set(top5) & forbidden:
                forb_n += 1

    return {
        "recall_at_1": MetricValue(r1_n, r1_d, excluded_empty),
        "recall_at_5": MetricValue(r5_n, r5_d, excluded_empty),
        "mrr_at_10": MetricValue(mrr_sum, mrr_d, excluded_empty),
        "ndcg_at_10": MetricValue(ndcg_sum, ndcg_d, excluded_empty),
        "precision_at_5": MetricValue(p5_sum, p5_d, excluded_empty),
        "forbidden_hit_rate_at_5": MetricValue(forb_n, forb_d, 0),
        "excluded_empty_expected": excluded_empty,
    }


def score_no_answer(rows: list[dict[str, Any]]) -> dict[str, Any]:
    correct = total = 0
    false_answer = 0
    false_pos_retrieval = 0
    false_neg_refusal = 0

    for row in rows:
        if not row.get("expected_no_answer"):
            continue
        total += 1
        search_no = bool(row.get("search_no_match"))
        mode = (row.get("ask_answer_mode") or row.get("answer_mode") or "").lower()
        answer = (row.get("answer") or "").strip()
        sources = row.get("sources") or []

        is_refusal = (
            search_no
            or mode in {"no_answer", "no_match", "refuse", "refusal"}
            or (not answer and not sources)
        )
        if is_refusal and not answer:
            correct += 1
        else:
            false_answer += 1
            if sources or answer:
                false_pos_retrieval += 1
            if answer and mode not in {"no_answer", "no_match"}:
                false_neg_refusal += 1

    return {
        "no_answer_accuracy": MetricValue(correct, total),
        "false_answer_rate": MetricValue(false_answer, total),
        "false_positive_retrieval_rate": MetricValue(false_pos_retrieval, total),
        "false_negative_refusal_rate": MetricValue(false_neg_refusal, total),
    }


def score_answer_citations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = complete_d = 0
    cit_comp_n = cit_comp_d = 0
    cit_corr_n = cit_corr_d = 0
    unsup_n = unsup_d = 0
    src_valid_n = src_valid_d = 0
    src_prec_sum = src_prec_d = 0.0
    src_rec_sum = src_rec_d = 0.0
    excluded_no_answer = 0

    for row in rows:
        mode = (row.get("answer_mode") or "").lower()
        answer = (row.get("answer") or "").strip()
        facts = list(row.get("expected_answer_facts") or [])
        if mode in {"no_answer", "no_match", "refuse"} or (not answer and not facts):
            excluded_no_answer += 1
            continue
        if not facts:
            # no human evidence → exclude from citation correctness
            excluded_no_answer += 1
            continue

        complete_d += 1
        if answer:
            completed += 1

        sources = row.get("sources") or []
        source_ids = []
        for s in sources:
            if isinstance(s, dict):
                kid = s.get("knowledge_id") or s.get("id") or s.get("source_id")
                if kid:
                    source_ids.append(str(kid))
            elif s:
                source_ids.append(str(s))

        support_all: set[str] = set()
        for fact in facts:
            support = set(fact.get("supporting_knowledge_ids") or [])
            support_all |= support
            cit_comp_d += 1
            # Completeness: fact has at least one supporting source in cited sources
            if set(source_ids) & support:
                cit_comp_n += 1

            cit_corr_d += 1
            if set(source_ids) & support:
                cit_corr_n += 1

        forbidden = list(row.get("forbidden_claims") or [])
        detected = list(row.get("unsupported_claims_detected") or [])
        for fc in forbidden:
            if fc and fc in answer and fc not in detected:
                detected.append(fc)
        if forbidden or detected:
            unsup_d += max(len(forbidden), 1)
            unsup_n += len(detected)
        else:
            # Applicable answered row with no forbidden list: 0 unsupported / 1
            unsup_d += 1
            unsup_n += 0

        # source id validity: non-empty string ids only (existence checked by harness)
        if source_ids:
            src_valid_d += len(source_ids)
            src_valid_n += sum(1 for x in source_ids if x and x != "None")
            if support_all:
                src_prec_d += 1
                src_prec_sum += len(set(source_ids) & support_all) / len(set(source_ids))
                src_rec_d += 1
                src_rec_sum += len(set(source_ids) & support_all) / len(support_all)

    return {
        "answer_completion_rate": MetricValue(completed, complete_d, excluded_no_answer),
        "citation_completeness": MetricValue(cit_comp_n, cit_comp_d, excluded_no_answer),
        "citation_correctness": MetricValue(cit_corr_n, cit_corr_d, excluded_no_answer),
        "unsupported_claim_rate": MetricValue(unsup_n, unsup_d if unsup_d else 0, excluded_no_answer),
        "source_precision": MetricValue(src_prec_sum, int(src_prec_d), excluded_no_answer),
        "source_recall": MetricValue(src_rec_sum, int(src_rec_d), excluded_no_answer),
        "source_id_validity": MetricValue(src_valid_n, src_valid_d, excluded_no_answer),
        "excluded_no_answer_or_no_facts": excluded_no_answer,
    }


def _unit_present(text: str, unit: str, other_units: list[str] | None = None) -> bool:
    """Match unit tokens; longer compound units (珠/米) take precedence over 米."""
    if not text or not unit:
        return False
    others = sorted([u for u in (other_units or []) if u and u != unit], key=len, reverse=True)
    # Mask longer units first so "珠/米" is not counted as "米"
    masked = text
    for ou in others:
        if len(ou) > len(unit) and ou in masked:
            masked = masked.replace(ou, " " * len(ou))
    return unit in masked


def score_numeric_units(rows: list[dict[str, Any]]) -> dict[str, Any]:
    top1_n = top1_d = 0
    top3_n = top3_d = 0
    conf_n = conf_d = 0
    forb_doc_n = forb_doc_d = 0
    noa_n = noa_d = 0

    for row in rows:
        expected_no = bool(row.get("expected_no_answer"))
        expected_ids = set(row.get("expected_ids") or [])
        expected_units = list(row.get("expected_units") or [])
        forbidden_units = list(row.get("forbidden_units") or [])
        forbidden_ids = set(row.get("forbidden_ids") or [])
        got_ids = list(row.get("got_ids") or [])
        texts = list(row.get("got_top_texts") or [])
        top_text = texts[0] if texts else ""
        top3_text = " ".join(texts[:3])
        all_units = expected_units + forbidden_units

        if expected_no:
            noa_d += 1
            empty = not got_ids and not any(t.strip() for t in texts)
            no_match = bool(row.get("search_no_match")) or empty
            if no_match:
                noa_n += 1
            continue

        # expected hit path
        if expected_units or expected_ids:
            top1_d += 1
            unit_ok = False
            if got_ids or texts:
                if expected_units:
                    unit_ok = any(
                        _unit_present(top_text, u, all_units)
                        or _unit_present(top3_text, u, all_units)
                        for u in expected_units
                    )
                    # if only ids and no text, fall back to id hit
                    if not texts and expected_ids and set(got_ids[:1]) & expected_ids:
                        unit_ok = True
                elif expected_ids:
                    unit_ok = bool(set(got_ids[:1]) & expected_ids)
            # empty => fail
            if unit_ok:
                top1_n += 1

            if expected_ids:
                top3_d += 1
                if set(got_ids[:3]) & expected_ids:
                    top3_n += 1
            elif expected_units:
                top3_d += 1
                if any(_unit_present(top3_text, u, all_units) for u in expected_units):
                    top3_n += 1

            if forbidden_units:
                conf_d += 1
                has_forb = any(
                    _unit_present(top_text, u, all_units)
                    or _unit_present(top3_text, u, all_units)
                    for u in forbidden_units
                )
                has_exp = (
                    any(_unit_present(top_text, u, all_units) for u in expected_units)
                    if expected_units
                    else bool(set(got_ids[:3]) & expected_ids)
                )
                if has_forb and not has_exp:
                    conf_n += 1

            if forbidden_ids:
                forb_doc_d += 1
                if set(got_ids[:5]) & forbidden_ids:
                    forb_doc_n += 1

    return {
        "top1_unit_accuracy": MetricValue(top1_n, top1_d),
        "top3_expected_document_recall": MetricValue(top3_n, top3_d),
        "forbidden_unit_confusion_rate": MetricValue(conf_n, conf_d),
        "forbidden_document_hit_rate": MetricValue(forb_doc_n, forb_doc_d),
        "numeric_no_answer_accuracy": MetricValue(noa_n, noa_d),
    }


def score_routing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mode_n = mode_d = 0
    tool_n = tool_d = 0
    arg_n = arg_d = 0
    proto_n = proto_d = 0
    task_n = task_d = 0
    tfree_n = tfree_d = 0
    raw_arg_n = raw_arg_d = 0
    empty_honesty_n = empty_honesty_d = 0
    timeout_class_n = timeout_class_d = 0
    flow_n = flow_d = 0

    for row in rows:
        exp_mode = (row.get("expected_mode") or "").lower()
        exp_tool = (row.get("expected_tool") or "").lower()
        got_mode = (row.get("got_mode") or "").lower()
        got_tool = (row.get("got_tool") or "").lower()
        req_keys = list(row.get("required_argument_keys") or [])
        args = row.get("got_arguments") or row.get("recommended_arguments_raw") or {}
        protocol_ok = bool(row.get("protocol_ok"))
        timed_out = bool(row.get("timed_out"))
        exp_outcome = row.get("expected_task_outcome")
        got_outcome = row.get("task_outcome")

        mode_d += 1
        if exp_mode and got_mode == exp_mode:
            mode_n += 1

        tool_d += 1
        if exp_tool and got_tool == exp_tool:
            tool_n += 1

        arg_d += 1
        argument_contract = row.get("argument_contract")
        if isinstance(argument_contract, dict):
            argument_ok = all(
                bool(argument_contract.get(key))
                for key in (
                    "required_keys_present",
                    "types_valid",
                    "raw_equals_executed",
                )
            ) and argument_contract.get("forbidden_keys_absent", True) is not False
        else:
            argument_ok = all(k in args for k in req_keys)
        if argument_ok:
            arg_n += 1

        proto_d += 1
        if protocol_ok:
            proto_n += 1

        task_d += 1
        successful_outcomes = {"non_empty", "no_answer", "graph_result", "structured_result"}
        task_completed = row.get("task_completed")
        if task_completed is None:
            task_completed = (
                protocol_ok
                and not timed_out
                and got_outcome in successful_outcomes
            )
        if task_completed and got_outcome == exp_outcome:
            task_n += 1

        tfree_d += 1
        if (not timed_out) and task_completed and got_outcome == exp_outcome:
            tfree_n += 1

        if "arguments_exact_match" in row:
            raw_arg_d += 1
            if row.get("arguments_exact_match") is True:
                raw_arg_n += 1

        if row.get("empty_result_detected") is True:
            empty_honesty_d += 1
            if got_outcome == "empty" and not row.get("task_completed"):
                empty_honesty_n += 1

        if row.get("timeout_signal_detected") is True:
            timeout_class_d += 1
            if timed_out and got_outcome == "timeout" and not row.get("task_completed"):
                timeout_class_n += 1

        if row.get("recommended_flow"):
            flow_d += 1
            if row.get("recommended_flow_executed") is True:
                flow_n += 1

    return {
        "mode_accuracy": MetricValue(mode_n, mode_d),
        "recommended_tool_accuracy": MetricValue(tool_n, tool_d),
        "argument_contract_accuracy": MetricValue(arg_n, arg_d),
        "protocol_execution_rate": MetricValue(proto_n, proto_d),
        "task_completion_rate": MetricValue(task_n, task_d),
        "timeout_free_completion_rate": MetricValue(tfree_n, tfree_d),
        "raw_argument_preservation_rate": MetricValue(raw_arg_n, raw_arg_d),
        "empty_result_honesty_rate": MetricValue(empty_honesty_n, empty_honesty_d),
        "timeout_classification_accuracy": MetricValue(timeout_class_n, timeout_class_d),
        "recommended_flow_execution_rate": MetricValue(flow_n, flow_d),
    }


def metrics_to_jsonable(metrics: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in metrics.items():
        if isinstance(v, MetricValue):
            out[k] = v.as_dict()
        else:
            out[k] = v
    return out
