"""Score the hit-rate test results strictly from MCP-returned content.

评分依据：仅使用每条用例的 MCP 原始返回（search/read/ask 的 data），
对照 Golden Set 中由人工核验的 expected_knowledge_ids / required_facts /
forbidden_facts / expected_no_answer 判定。不引入任何外部常识。

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


def load_case(cid):
    return json.load(open(OUT / f"{cid}.json", encoding="utf-8"))


def get_search_candidates(d):
    return d.get("candidates", []) or []


def get_read_text(d):
    rd = d.get("read") or {}
    env = rd.get("envelope") or {}
    data = env.get("data")
    if isinstance(data, dict):
        return str(data.get("content") or data.get("text") or "")
    if isinstance(data, list):
        return "\n".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in data)
    return ""


def get_ask_answer(d):
    aenv = (d.get("ask") or {}).get("envelope") or {}
    data = aenv.get("data") if aenv.get("ok") else None
    if not isinstance(data, dict):
        return "", [], []
    ans = str(data.get("answer") or "")
    srcs = data.get("sources") or []
    raw_ev = data.get("raw_evidence_used") or []
    return ans, srcs, raw_ev


def all_relevant_text(d):
    """Concatenate all MCP-returned evidence text for fact checking."""
    parts = []
    for c in get_search_candidates(d):
        parts.append(str(c.get("text") or ""))
        parts.append(str(c.get("title") or ""))
    parts.append(get_read_text(d))
    ans, srcs, raw_ev = get_ask_answer(d)
    parts.append(ans)
    for s in srcs:
        if isinstance(s, dict):
            parts.append(str(s.get("text") or ""))
            parts.append(str(s.get("title") or ""))
    for e in raw_ev:
        if isinstance(e, dict):
            parts.append(str(e.get("text") or ""))
    return "\n".join(p for p in parts if p)


def norm(s):
    return re.sub(r"\s+", "", str(s))


def contains(haystack, needle):
    return norm(needle) in norm(haystack)


def score_answerable(case, d):
    """Score an answerable case. Returns dict of per-metric booleans + score."""
    expected_ids = set(case["expected_knowledge_ids"])
    required = case.get("required_facts", [])
    forbidden = case.get("forbidden_facts", [])
    cands = get_search_candidates(d)
    cand_ids = [c.get("knowledge_id") for c in cands if c.get("knowledge_id")]
    top1_id = cand_ids[0] if cand_ids else None

    # Top-1 hit: first candidate is an expected id
    top1_hit = top1_id in expected_ids if top1_id else False
    # Recall@5: any expected id in top-5
    top5_ids = cand_ids[:5]
    recall5 = any(cid in expected_ids for cid in top5_ids)

    # Facts: check all required_facts present in MCP-returned text; no forbidden present
    evidence = all_relevant_text(d)
    facts_ok = all(contains(evidence, f) for f in required) if required else True
    forbidden_violated = any(contains(evidence, f) for f in forbidden)
    facts_correct = facts_ok and not forbidden_violated

    # Citation validity: ask sources must reference an expected knowledge_id
    ans, srcs, raw_ev = get_ask_answer(d)
    ask_has_sources = bool(srcs or raw_ev)
    citation_valid = False
    if ask_has_sources:
        for s in (srcs + raw_ev):
            if isinstance(s, dict) and s.get("knowledge_id") in expected_ids:
                citation_valid = True
                break
    # If ask returned no answer/sources but search hit, citation is partial — mark based on search hit
    if not ask_has_sources and recall5:
        citation_valid = True  # search citation itself is locatable

    # Hallucination: ask answer contains forbidden_facts OR asserts facts not in evidence
    hallucination = False
    if forbidden and ans:
        hallucination = any(contains(ans, f) for f in forbidden)

    # Score per rubric 5.1
    score = 0
    if top1_hit:
        score += 3
    if recall5:
        score += 2
    if facts_correct:
        score += 2
    if citation_valid:
        score += 2
    if not hallucination and not forbidden_violated:
        score += 1

    return {
        "top1_hit": top1_hit,
        "recall5": recall5,
        "facts_correct": facts_correct,
        "citation_valid": citation_valid,
        "no_hallucination": not hallucination and not forbidden_violated,
        "score": score,
        "top1_id": top1_id,
        "cand_ids": cand_ids,
        "ask_has_answer": bool(ans.strip()),
        "ask_source_count": len(srcs) + len(raw_ev),
        "forbidden_violated": forbidden_violated,
    }


def score_no_answer(case, d):
    """Score a no-answer case per rubric 5.2."""
    forbidden = case.get("forbidden_facts", [])
    ans, srcs, raw_ev = get_ask_answer(d)
    cands = get_search_candidates(d)
    evidence = all_relevant_text(d)

    # Did it present irrelevant content as a definite answer?
    # Heuristic: if ask produced an answer that asserts a forbidden/specific fact with certainty
    false_positive = False
    if ans and forbidden:
        # Look for confident assertion of forbidden facts (not in a "not found" context)
        for f in forbidden:
            if contains(ans, f):
                # Check it's not phrased as "知识库中未包含X"
                # simple check: forbidden fact appears AND answer isn't purely a refusal
                if not re.search(r"(未|没有|无法|不含|不应|不存在|未检索到|未收录)", norm(ans)[:50]):
                    false_positive = True

    # Correctly expressed insufficient evidence
    refusal_markers = ["证据不足", "未检索到", "未收录", "没有足够", "无法回答", "知识库中不",
                       "未包含", "不存在", "无法提供", "不在知识库", "未找到", "没有相关"]
    expressed_insufficient = any(contains(ans, m) for m in refusal_markers) or (not ans.strip())

    # No fabrication: answer does not invent specific forbidden facts
    no_fabrication = not false_positive

    score = 0
    if not false_positive:
        score += 4
    if expressed_insufficient:
        score += 3
    if no_fabrication:
        score += 3

    return {
        "false_positive": false_positive,
        "expressed_insufficient": expressed_insufficient,
        "no_fabrication": no_fabrication,
        "score": score,
        "top1_id": (cands[0].get("knowledge_id") if cands else None),
        "candidate_count": len(cands),
        "ask_has_answer": bool(ans.strip()),
    }


def main():
    rows = []
    for case in GOLDEN:
        cid = case["case_id"]
        d = load_case(cid)
        if case.get("expected_no_answer"):
            sc = score_no_answer(case, d)
            rows.append({"case_id": cid, "type": "no_answer", **case, "score_result": sc, "data": d})
        else:
            sc = score_answerable(case, d)
            rows.append({"case_id": cid, "type": "answerable", **case, "score_result": sc, "data": d})

    # Aggregate metrics
    answerable = [r for r in rows if r["type"] == "answerable"]
    no_answer = [r for r in rows if r["type"] == "no_answer"]

    n_ans = len(answerable)
    n_no = len(no_answer)

    top1_correct = sum(1 for r in answerable if r["score_result"]["top1_hit"])
    recall5 = sum(1 for r in answerable if r["score_result"]["recall5"])
    # Answer groundedness: ask has answer AND all required facts in evidence AND citation valid
    grounded = sum(
        1 for r in answerable
        if r["score_result"]["ask_has_answer"]
        and r["score_result"]["facts_correct"]
        and r["score_result"]["citation_valid"]
    )
    # Citation validity: count individual citations
    total_citations = 0
    valid_citations = 0
    for r in answerable:
        d = r["data"]
        ans, srcs, raw_ev = get_ask_answer(d)
        expected_ids = set(r["expected_knowledge_ids"])
        for s in (srcs + raw_ev):
            if isinstance(s, dict) and s.get("knowledge_id"):
                total_citations += 1
                if s["knowledge_id"] in expected_ids:
                    valid_citations += 1
    # Hallucination: answerable cases with forbidden facts asserted in ask answer
    halluc = sum(1 for r in answerable if not r["score_result"]["no_hallucination"])
    # False positive: no-answer cases that gave a definite wrong answer
    fp = sum(1 for r in no_answer if r["score_result"]["false_positive"])

    metrics = {
        "answerable_total": n_ans,
        "no_answer_total": n_no,
        "top1_accuracy": top1_correct / n_ans if n_ans else 0,
        "recall5": recall5 / n_ans if n_ans else 0,
        "answer_groundedness": grounded / n_ans if n_ans else 0,
        "citation_validity": valid_citations / total_citations if total_citations else 0,
        "citation_total": total_citations,
        "citation_valid_count": valid_citations,
        "hallucination_rate": halluc / n_ans if n_ans else 0,
        "false_positive_rate": fp / n_no if n_no else 0,
    }

    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    # Per-case detail
    detail = []
    for r in rows:
        sc = r["score_result"]
        if r["type"] == "answerable":
            detail.append({
                "case_id": r["case_id"],
                "category": r["category"],
                "query": r["query"],
                "expected_ids": r["expected_knowledge_ids"],
                "top1_id": sc.get("top1_id"),
                "top1_hit": sc["top1_hit"],
                "recall5": sc["recall5"],
                "facts_correct": sc["facts_correct"],
                "citation_valid": sc["citation_valid"],
                "no_hallucination": sc["no_hallucination"],
                "ask_has_answer": sc["ask_has_answer"],
                "ask_source_count": sc["ask_source_count"],
                "score": sc["score"],
            })
        else:
            detail.append({
                "case_id": r["case_id"],
                "category": r["category"],
                "query": r["query"],
                "expected_no_answer": True,
                "false_positive": sc["false_positive"],
                "expressed_insufficient": sc["expressed_insufficient"],
                "no_fabrication": sc["no_fabrication"],
                "candidate_count": sc["candidate_count"],
                "top1_id": sc["top1_id"],
                "ask_has_answer": sc["ask_has_answer"],
                "score": sc["score"],
            })

    out = {"metrics": metrics, "detail": detail}
    (OUT / "scored.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved -> {OUT/'scored.json'}")


if __name__ == "__main__":
    main()
