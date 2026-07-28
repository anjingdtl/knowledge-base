"""Finalize scoring: produce per-case verdicts + metrics for the remediation report.

严格依据 MCP 返回内容判定。SPEC v2：Ask Fact Correctness 仅看 ask.answer；
Citation Validity 仅看 ask.sources 相对预接受/相邻扩展/期望 ID。

可通过环境变量 ``HIT_RATE_ARTIFACTS_DIR`` 指定 artifacts 目录（默认
``artifacts/hit_rate_test`` 为基线，复测应指向独立目录以免覆盖基线）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

OUT = Path(os.environ.get("HIT_RATE_ARTIFACTS_DIR", "artifacts/hit_rate_test"))
GOLDEN = json.load(open("evals/golden_set_hit_rate.json", encoding="utf-8"))["cases"]


def load(cid):
    return json.load(open(OUT / f"{cid}.json", encoding="utf-8"))


def norm(s):
    return re.sub(r"\s+", "", str(s))


def contains(hay, needle):
    return norm(needle) in norm(hay)


def ask_info(d):
    aenv = (d.get("ask") or {}).get("envelope") or {}
    data = aenv.get("data") if aenv.get("ok") else None
    if not isinstance(data, dict):
        return {
            "ok": False, "answer": "", "mode": None, "warnings": [],
            "sources": [], "raw_ev": [], "top_score": None, "snap": {},
            "citation_integrity": {},
        }
    return {
        "ok": True,
        "answer": str(data.get("answer") or ""),
        "mode": data.get("answer_mode"),
        "warnings": data.get("warnings") or [],
        "sources": data.get("sources") or [],
        "raw_ev": data.get("raw_evidence_used") or [],
        "top_score": _extract_top_score(data.get("warnings") or []),
        "snap": data.get("evidence_snapshot") or {},
        "citation_integrity": data.get("citation_integrity") or {},
    }


def _extract_top_score(warnings):
    for w in warnings:
        if isinstance(w, str):
            m = re.search(r"top_score=([0-9.]+)", w)
            if m:
                return float(m.group(1))
    return None


def classify_defect(case, d, sc):
    """Return (severity, category, reason) for failed/partial cases."""
    a = ask_info(d)
    expected = case["expected_knowledge_ids"]
    n_cand = sc.get("cand_count", 0)

    if any("requires_current_external_data" in str(w) for w in a["warnings"]):
        return (
            "P1", "routing",
            "意图误判为 requires_current_external_data，未执行检索即返回 no_answer。",
        )
    if n_cand == 0 and expected:
        return (
            "P1", "retrieval_recall",
            "search 返回 0 候选；相关文档未被召回。",
        )
    if sc.get("recall5") and a["mode"] == "no_answer" and any(
        "evidence gate" in str(w) for w in a["warnings"]
    ):
        ts = a["top_score"]
        return (
            "P1", "answer_pipeline",
            f"search 已命中正确文档，但 ask 的 evidence gate（top_score={ts}）拦截生成。",
        )
    if not sc.get("no_hallucination"):
        return ("P1", "hallucination", "回答包含 forbidden_facts 或无依据扩写。")
    if sc.get("recall5") and not sc.get("ask_fact_correct"):
        return (
            "P1", "answer_fact",
            "search 召回正确文档，但 ask.answer 未覆盖 required_facts 或含 forbidden。",
        )
    if sc.get("ask_fact_correct") and not sc.get("ask_citation_valid"):
        return (
            "P2", "citation_integrity",
            "最终答案事实正确，但 ask.sources 含不可追溯引用。",
        )
    if not sc.get("top1_hit") and sc.get("recall5") and not sc.get("wrong_version_in_evidence"):
        return ("P2", "ranking", "正确文档进入 Top5 但非 Top1。")
    if sc.get("wrong_version_in_evidence") and not sc.get("top1_hit"):
        return (
            "P2", "version_ranking",
            "检索优先返回旧版/易混淆事实文档，正确版本靠后或未召回。",
        )
    return (None, None, None)


def score_answerable(case, d):
    expected = set(case["expected_knowledge_ids"])
    required = case.get("required_facts", [])
    forbidden = case.get("forbidden_facts", [])
    cands = d.get("candidates", [])
    cand_ids = [c.get("knowledge_id") for c in cands if c.get("knowledge_id")]
    top1_id = cand_ids[0] if cand_ids else None
    top1_hit = top1_id in expected if top1_id else False
    recall5 = any(cid in expected for cid in cand_ids[:5])

    a = ask_info(d)
    ans = a["answer"]
    # SPEC v2: ask fact correctness from answer text ONLY
    ans_has_required = all(contains(ans, f) for f in required) if required else bool(ans.strip())
    ans_has_forbidden = (
        any(contains(ans, f) for f in forbidden) if forbidden and ans.strip() else False
    )
    ask_fact_correct = bool(ans.strip()) and ans_has_required and not ans_has_forbidden
    hallucination = ans_has_forbidden

    # Citation validity on ask.sources only
    srcs = [s for s in a["sources"] if isinstance(s, dict)]
    snap = a["snap"]
    accepted_kids = set(snap.get("accepted_knowledge_ids") or [])
    accepted_blocks = set(snap.get("accepted_block_ids") or [])
    buckets = {"preaccepted": 0, "adjacent_extension": 0, "expected_id": 0, "rejected": 0}
    for s in srcs:
        kid = str(s.get("knowledge_id") or "").strip()
        bid = str(s.get("block_id") or "").strip()
        if s.get("is_adjacent_extension"):
            buckets["adjacent_extension"] += 1
        elif kid and kid in accepted_kids:
            buckets["preaccepted"] += 1
        elif bid and bid in accepted_blocks:
            buckets["preaccepted"] += 1
        elif kid and kid in expected:
            buckets["expected_id"] += 1
        else:
            buckets["rejected"] += 1
    if srcs:
        valid_n = buckets["preaccepted"] + buckets["adjacent_extension"] + buckets["expected_id"]
        ask_citation_valid = valid_n == len(srcs)
        citation_valid_num = sum(1 for s in srcs if s.get("knowledge_id") in expected)
        citation_valid_den = len(srcs)
    else:
        ask_citation_valid = False
        citation_valid_num = 0
        citation_valid_den = 0

    e2e_pass = (
        recall5 and ask_fact_correct and ask_citation_valid and not hallucination
    )
    grounded = ask_fact_correct and ask_citation_valid
    facts_correct = ask_fact_correct
    # Wrong-version evidence in search candidates (for defect classification)
    search_blob = "\n".join(
        f"{c.get('title','')} {c.get('text','')}" for c in cands if isinstance(c, dict)
    )
    wrong_version_in_evidence = any(contains(search_blob, f) for f in forbidden) if forbidden else False

    score = 0
    if top1_hit:
        score += 3
    if recall5:
        score += 2
    if ask_fact_correct:
        score += 2
    if ask_citation_valid:
        score += 2
    if not hallucination:
        score += 1

    sc = {
        "top1_hit": top1_hit,
        "recall5": recall5,
        "facts_correct": facts_correct,
        "ask_fact_correct": ask_fact_correct,
        "citation_valid": ask_citation_valid,
        "ask_citation_valid": ask_citation_valid,
        "no_hallucination": not hallucination,
        "grounded": grounded,
        "e2e_pass": e2e_pass,
        "score": score,
        "top1_id": top1_id,
        "cand_count": len(cands),
        "wrong_version_in_evidence": wrong_version_in_evidence,
        "citation_buckets": buckets,
        "citation_valid_num": citation_valid_num,
        "citation_valid_den": citation_valid_den,
        "ask_source_count": len(srcs),
        "ask_has_answer": bool(ans.strip()),
    }
    sev, cat, reason = classify_defect(case, d, sc)
    sc["defect_severity"] = sev
    sc["defect_category"] = cat
    sc["defect_reason"] = reason
    return sc


def score_no_answer(case, d):
    forbidden = case.get("forbidden_facts", [])
    a = ask_info(d)
    ans = a["answer"]
    false_positive = False
    if ans and forbidden:
        for f in forbidden:
            if contains(ans, f) and not re.search(
                r"(未|没有|无法|不含|不应|不存在|未检索到|未收录)", norm(ans)[:80]
            ):
                false_positive = True
    refusal = [
        "证据不足", "未检索到", "未收录", "没有足够", "无法回答", "知识库中不",
        "未包含", "不存在", "无法提供", "不在知识库", "未找到", "没有相关",
        "超出.*范围", "与.*无关", "未能确认", "未找到可回答",
    ]
    expressed = any(re.search(r, norm(ans)) for r in refusal) or (not ans.strip())
    no_fab = not false_positive
    score = (4 if not false_positive else 0) + (3 if expressed else 0) + (3 if no_fab else 0)
    return {
        "false_positive": false_positive,
        "expressed_insufficient": expressed,
        "no_fabrication": no_fab,
        "score": score,
        "top1_id": (
            d.get("candidates", [{}])[0].get("knowledge_id") if d.get("candidates") else None
        ),
        "cand_count": len(d.get("candidates", [])),
        "ask_has_answer": bool(ans.strip()),
    }


def main():
    details = []
    defects = {"P0": [], "P1": [], "P2": [], "P3": []}
    answerable = []
    no_answer = []

    cite_num = 0
    cite_den = 0
    bucket_totals = {
        "preaccepted": 0, "adjacent_extension": 0, "expected_id": 0, "rejected": 0,
    }

    for case in GOLDEN:
        cid = case["case_id"]
        path = OUT / f"{cid}.json"
        if not path.exists():
            print(f"MISSING {cid}")
            continue
        d = load(cid)
        if case.get("expected_no_answer"):
            sc = score_no_answer(case, d)
            no_answer.append(sc)
            details.append({
                "case_id": cid,
                "category": case.get("category"),
                "query": case.get("query"),
                "type": "no_answer",
                **sc,
                "defect_severity": "P1" if sc.get("false_positive") else None,
                "defect_category": "false_positive" if sc.get("false_positive") else None,
                "defect_reason": "no-answer 用例给出确定性错误答案" if sc.get("false_positive") else None,
            })
            if sc.get("false_positive"):
                defects["P1"].append(cid)
        else:
            sc = score_answerable(case, d)
            answerable.append(sc)
            details.append({
                "case_id": cid,
                "category": case.get("category"),
                "query": case.get("query"),
                "expected_ids": case.get("expected_knowledge_ids"),
                "required_facts": case.get("required_facts"),
                "forbidden_facts": case.get("forbidden_facts"),
                "type": "answerable",
                **sc,
            })
            sev = sc.get("defect_severity")
            if sev:
                defects[sev].append(cid)
            cite_num += sc.get("citation_valid_num", 0)
            cite_den += sc.get("citation_valid_den", 0)
            for k, v in (sc.get("citation_buckets") or {}).items():
                bucket_totals[k] = bucket_totals.get(k, 0) + v

    n_ans = len(answerable) or 1
    n_no = len(no_answer) or 1
    top1 = sum(1 for s in answerable if s.get("top1_hit"))
    recall5 = sum(1 for s in answerable if s.get("recall5"))
    ask_fact = sum(1 for s in answerable if s.get("ask_fact_correct"))
    ask_cite = sum(1 for s in answerable if s.get("ask_citation_valid"))
    e2e = sum(1 for s in answerable if s.get("e2e_pass"))
    grounded = sum(1 for s in answerable if s.get("grounded"))
    hall = sum(1 for s in answerable if not s.get("no_hallucination"))
    fp = sum(1 for s in no_answer if s.get("false_positive"))

    metrics = {
        "answerable_total": len(answerable),
        "no_answer_total": len(no_answer),
        "top1_correct": top1,
        "recall5_correct": recall5,
        "ask_fact_correct_count": ask_fact,
        "ask_citation_valid_count": ask_cite,
        "e2e_pass_count": e2e,
        "grounded_count": grounded,
        "Top-1 Accuracy": round(top1 / n_ans, 4),
        "Recall@5": round(recall5 / n_ans, 4),
        "Ask Fact Correctness": round(ask_fact / n_ans, 4),
        "Ask Citation Validity": round(ask_cite / n_ans, 4),
        "E2E Pass Rate": round(e2e / n_ans, 4),
        # Legacy aliases for compare scripts
        "Answer Groundedness": round(grounded / n_ans, 4),
        "Citation Validity": round(cite_num / cite_den, 4) if cite_den else 0.0,
        "citation_total": cite_den,
        "citation_valid": cite_num,
        "citation_buckets": bucket_totals,
        "Hallucination Rate": round(hall / n_ans, 4),
        "False Positive Rate": round(fp / n_no, 4),
    }

    # Gate against SPEC v2 minimums
    gates = {
        "Top-1 Accuracy": (0.75, True),
        "Recall@5": (0.88, True),
        "Ask Fact Correctness": (0.90, True),
        "Ask Citation Validity": (0.95, True),
        "E2E Pass Rate": (0.90, True),
        "Hallucination Rate": (0.05, False),
        "False Positive Rate": (0.05, False),
    }
    gate_results = {}
    all_pass = True
    for key, (thr, higher) in gates.items():
        val = metrics.get(key, 0.0)
        ok = (val >= thr) if higher else (val <= thr)
        gate_results[key] = {"value": val, "threshold": thr, "pass": ok}
        if not ok:
            all_pass = False

    out = {
        "metrics": metrics,
        "gates": gate_results,
        "release_verdict": "通过放行" if all_pass else "不通过放行",
        "defects": defects,
        "detail": details,
    }
    (OUT / "final_scored.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"metrics": metrics, "release_verdict": out["release_verdict"], "defects": defects}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
