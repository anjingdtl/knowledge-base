"""Finalize scoring: produce per-case verdicts + raw evidence snippets for report.

严格依据 MCP 返回内容判定。对 ask 被 evidence-gate 拦截但 search 命中的情形，
单独标注为 answer-pipeline 缺陷（而非 retrieval 缺陷）。

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
        return {"ok": False, "answer": "", "mode": None, "warnings": [], "sources": [], "raw_ev": [], "top_score": None}
    return {
        "ok": True,
        "answer": str(data.get("answer") or ""),
        "mode": data.get("answer_mode"),
        "warnings": data.get("warnings") or [],
        "sources": data.get("sources") or [],
        "raw_ev": data.get("raw_evidence_used") or [],
        "top_score": _extract_top_score(data.get("warnings") or []),
    }


def _extract_top_score(warnings):
    for w in warnings:
        if isinstance(w, str):
            m = re.search(r"top_score=([0-9.]+)", w)
            if m:
                return float(m.group(1))
    return None


def read_text(d):
    rd = d.get("read") or {}
    env = rd.get("envelope") or {}
    data = env.get("data")
    if isinstance(data, dict):
        return str(data.get("content") or data.get("text") or "")
    if isinstance(data, list):
        return "\n".join(str((x or {}).get("text", "")) if isinstance(x, dict) else str(x) for x in data)
    return ""


def all_text(d):
    parts = []
    for c in d.get("candidates", []):
        parts.append(str(c.get("text") or "")); parts.append(str(c.get("title") or ""))
    parts.append(read_text(d))
    a = ask_info(d)
    parts.append(a["answer"])
    for s in a["sources"] + a["raw_ev"]:
        if isinstance(s, dict):
            parts.append(str(s.get("text") or ""))
    return "\n".join(p for p in parts if p)


def classify_defect(case, d, sc):
    """Return (severity, category, reason) for failed/partial cases."""
    cid = case["case_id"]
    cands = d.get("candidates", [])
    a = ask_info(d)
    expected = case["expected_knowledge_ids"]
    n_cand = len(cands)

    # Routing misclassification (最新 -> requires_current_external_data)
    if any("requires_current_external_data" in str(w) for w in a["warnings"]):
        return ("P1", "routing", f"意图误判为 requires_current_external_data，未执行检索即返回 no_answer（耗时<30ms）。'最新' 触发 _CURRENT_INFO_RE。")

    # Search returned empty (retrieval recall failure)
    if n_cand == 0 and expected:
        return ("P1", "retrieval_recall", f"search 返回 0 候选；no_match_threshold=0.35 过滤掉全部结果，相关文档未被召回。")

    # Search found doc but ask blocked by evidence gate
    if sc.get("recall5") and a["mode"] == "no_answer" and any("evidence gate" in str(w) for w in a["warnings"]):
        ts = a["top_score"]
        return ("P1", "answer_pipeline", f"search 已命中正确文档，但 ask 的 evidence gate（top_score={ts} < 0.35）拦截生成。search 与 ask 评分不一致。")

    # Top-1 wrong but recall5 ok (ranking issue) — but not if it's a version conflict
    if not sc.get("top1_hit") and sc.get("recall5") and not sc.get("wrong_version_in_evidence"):
        return ("P2", "ranking", f"正确文档进入 Top5 但非 Top1；排序靠后。")

    # Wrong-version evidence surfaced (forbidden fact in retrieved evidence)
    if sc.get("wrong_version_in_evidence") and not sc.get("top1_hit"):
        return ("P2", "version_ranking", f"检索结果优先返回含旧版本/易混淆事实的文档，正确版本排序靠后或未召回。")

    # Hallucination / forbidden facts asserted in answer
    if not sc.get("no_hallucination"):
        return ("P1", "hallucination", f"回答包含 forbidden_facts 或无依据扩写。")

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
    evidence = all_text(d)
    facts_ok = all(contains(evidence, f) for f in required) if required else True
    forbidden_violated = any(contains(evidence, f) for f in forbidden)
    a = ask_info(d)
    citation_valid = False
    for s in a["sources"] + a["raw_ev"]:
        if isinstance(s, dict) and s.get("knowledge_id") in expected:
            citation_valid = True; break
    if not (a["sources"] or a["raw_ev"]) and recall5:
        citation_valid = True
    # Hallucination: forbidden fact asserted in the ask ANSWER (not just present in evidence)
    hallucination = bool(forbidden) and bool(a["answer"].strip()) and any(contains(a["answer"], f) for f in forbidden)
    # grounded requires a non-empty answer that covers required facts with valid citations,
    # and the answer does not assert any forbidden (wrong-version) fact.
    answer_asserts_forbidden = bool(forbidden) and bool(a["answer"].strip()) and any(contains(a["answer"], f) for f in forbidden)
    grounded = bool(a["answer"].strip()) and facts_ok and citation_valid and not answer_asserts_forbidden

    score = 0
    if top1_hit: score += 3
    if recall5: score += 2
    if facts_ok and not answer_asserts_forbidden: score += 2
    if citation_valid: score += 2
    if not hallucination: score += 1

    # facts_correct: required facts present; if forbidden (wrong-version) fact appears in the
    # generated answer, facts are considered incorrect. Empty answer doesn't violate.
    facts_correct = facts_ok and not (bool(a["answer"].strip()) and any(contains(a["answer"], f) for f in forbidden))
    # Track whether wrong-version evidence was surfaced (separate from hallucination)
    wrong_version_in_evidence = forbidden_violated
    no_hallucination = not hallucination

    sc = {
        "top1_hit": top1_hit, "recall5": recall5, "facts_correct": facts_correct,
        "citation_valid": citation_valid, "no_hallucination": no_hallucination,
        "grounded": grounded, "score": score, "top1_id": top1_id, "cand_count": len(cands),
        "wrong_version_in_evidence": wrong_version_in_evidence,
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
            if contains(ans, f) and not re.search(r"(未|没有|无法|不含|不应|不存在|未检索到|未收录)", norm(ans)[:60]):
                false_positive = True
    refusal = ["证据不足","未检索到","未收录","没有足够","无法回答","知识库中不","未包含","不存在","无法提供","不在知识库","未找到","没有相关","超出.*范围","与.*无关"]
    expressed = any(re.search(r, norm(ans)) for r in refusal) or (not ans.strip())
    no_fab = not false_positive
    score = (4 if not false_positive else 0) + (3 if expressed else 0) + (3 if no_fab else 0)
    return {
        "false_positive": false_positive, "expressed_insufficient": expressed,
        "no_fabrication": no_fab, "score": score,
        "top1_id": (d.get("candidates",[{}])[0].get("knowledge_id") if d.get("candidates") else None),
        "cand_count": len(d.get("candidates", [])),
    }


def main():
    rows = []
    for case in GOLDEN:
        cid = case["case_id"]
        d = load(cid)
        if case.get("expected_no_answer"):
            sc = score_no_answer(case, d)
            rows.append({"case": case, "data": d, "sc": sc, "type": "no_answer"})
        else:
            sc = score_answerable(case, d)
            rows.append({"case": case, "data": d, "sc": sc, "type": "answerable"})

    ans = [r for r in rows if r["type"] == "answerable"]
    no = [r for r in rows if r["type"] == "no_answer"]
    n_ans, n_no = len(ans), len(no)

    top1 = sum(1 for r in ans if r["sc"]["top1_hit"])
    r5 = sum(1 for r in ans if r["sc"]["recall5"])
    grounded = sum(1 for r in ans if r["sc"]["grounded"])

    # citation validity across all citations in answerable ask responses
    total_cit = valid_cit = 0
    for r in ans:
        a = ask_info(r["data"])
        exp = set(r["case"]["expected_knowledge_ids"])
        for s in a["sources"] + a["raw_ev"]:
            if isinstance(s, dict) and s.get("knowledge_id"):
                total_cit += 1
                if s["knowledge_id"] in exp:
                    valid_cit += 1

    halluc = sum(1 for r in ans if not r["sc"]["no_hallucination"])
    fp = sum(1 for r in no if r["sc"]["false_positive"])

    metrics = {
        "answerable_total": n_ans,
        "no_answer_total": n_no,
        "top1_correct": top1,
        "recall5_correct": r5,
        "grounded_count": grounded,
        "Top-1 Accuracy": round(top1 / n_ans, 4),
        "Recall@5": round(r5 / n_ans, 4),
        "Answer Groundedness": round(grounded / n_ans, 4),
        "Citation Validity": round(valid_cit / total_cit, 4) if total_cit else 0,
        "citation_total": total_cit,
        "citation_valid": valid_cit,
        "Hallucination Rate": round(halluc / n_ans, 4),
        "False Positive Rate": round(fp / n_no, 4) if n_no else 0,
    }

    # defect roll-up
    defects = {"P0": [], "P1": [], "P2": [], "P3": []}
    for r in ans:
        sev = r["sc"].get("defect_severity")
        if sev:
            defects[sev].append(r["case"]["case_id"])

    detail = []
    for r in rows:
        c, sc = r["case"], r["sc"]
        if r["type"] == "answerable":
            detail.append({
                "case_id": c["case_id"], "category": c["category"], "query": c["query"],
                "expected_ids": c["expected_knowledge_ids"], "required_facts": c.get("required_facts"),
                "forbidden_facts": c.get("forbidden_facts"),
                "top1_id": sc["top1_id"], "top1_hit": sc["top1_hit"], "recall5": sc["recall5"],
                "facts_correct": sc["facts_correct"], "citation_valid": sc["citation_valid"],
                "no_hallucination": sc["no_hallucination"], "grounded": sc["grounded"],
                "score": sc["score"], "defect_severity": sc.get("defect_severity"),
                "defect_category": sc.get("defect_category"), "defect_reason": sc.get("defect_reason"),
            })
        else:
            detail.append({
                "case_id": c["case_id"], "category": c["category"], "query": c["query"],
                "expected_no_answer": True, "false_positive": sc["false_positive"],
                "expressed_insufficient": sc["expressed_insufficient"], "no_fabrication": sc["no_fabrication"],
                "cand_count": sc["cand_count"], "score": sc["score"],
            })

    out = {"metrics": metrics, "defects": defects, "detail": detail}
    (OUT / "final_scored.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"metrics": metrics, "defects": {k: v for k, v in defects.items() if v}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
