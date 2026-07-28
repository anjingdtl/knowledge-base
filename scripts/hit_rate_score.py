"""Score the hit-rate test results strictly from MCP-returned content.

评分依据：仅使用每条用例的 MCP 原始返回（search/read/ask 的 data），
对照 Golden Set 中由人工核验的 expected_knowledge_ids / required_facts /
forbidden_facts / expected_no_answer 判定。不引入任何外部常识。

SPEC v2 Phase 5: 分离检索成功与最终回答正确性——禁止将 search/read/ask
文本混合后判断「最终回答正确」。

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
        return "", [], [], {}
    ans = str(data.get("answer") or "")
    srcs = data.get("sources") or []
    raw_ev = data.get("raw_evidence_used") or []
    snap = data.get("evidence_snapshot") or {}
    return ans, srcs, raw_ev, snap


def norm(s):
    return re.sub(r"\s+", "", str(s))


def contains(haystack, needle):
    return norm(needle) in norm(haystack)


def _citation_bucket(source, expected_ids, snap):
    """Classify a source as preaccepted / adjacent / rejected / expected."""
    if not isinstance(source, dict):
        return "rejected"
    kid = str(source.get("knowledge_id") or "").strip()
    bid = str(source.get("block_id") or "").strip()
    accepted_kids = set(snap.get("accepted_knowledge_ids") or [])
    accepted_blocks = set(snap.get("accepted_block_ids") or [])
    gen_kids = set(snap.get("generation_knowledge_ids") or [])
    if source.get("is_adjacent_extension"):
        return "adjacent_extension"
    if kid and kid in accepted_kids:
        return "preaccepted"
    if bid and bid in accepted_blocks:
        return "preaccepted"
    if kid and kid in gen_kids:
        return "preaccepted"
    if kid and kid in expected_ids:
        # Traceable to golden expected set even if snapshot missing (older artifacts).
        return "expected_id"
    return "rejected"


def score_answerable(case, d):
    """Score an answerable case with separated search/ask metrics (SPEC v2)."""
    expected_ids = set(case["expected_knowledge_ids"])
    required = case.get("required_facts", [])
    forbidden = case.get("forbidden_facts", [])
    cands = get_search_candidates(d)
    cand_ids = [c.get("knowledge_id") for c in cands if c.get("knowledge_id")]
    top1_id = cand_ids[0] if cand_ids else None

    # --- Search metrics (search.data / candidates only) ---
    top1_hit = top1_id in expected_ids if top1_id else False
    top5_ids = cand_ids[:5]
    recall5 = any(cid in expected_ids for cid in top5_ids)

    # --- Read verification (optional, informational) ---
    read_text = get_read_text(d)
    read_has_facts = (
        all(contains(read_text, f) for f in required) if required and read_text else False
    )

    # --- Ask fact correctness: ONLY ask.answer ---
    ans, srcs, raw_ev, snap = get_ask_answer(d)
    ans_has_required = all(contains(ans, f) for f in required) if required else bool(ans.strip())
    ans_has_forbidden = any(contains(ans, f) for f in forbidden) if forbidden and ans.strip() else False
    ask_fact_correct = bool(ans.strip()) and ans_has_required and not ans_has_forbidden

    # --- Ask citation validity: each source traceable ---
    citation_buckets = {"preaccepted": 0, "adjacent_extension": 0, "expected_id": 0, "rejected": 0}
    ask_sources = [s for s in srcs if isinstance(s, dict)]
    # Prefer final sources only (not raw_evidence) for citation validity (SPEC v2).
    if ask_sources:
        for s in ask_sources:
            bucket = _citation_bucket(s, expected_ids, snap)
            citation_buckets[bucket] = citation_buckets.get(bucket, 0) + 1
        # Valid = preaccepted / adjacent / expected_id; rejected is invalid.
        valid_n = (
            citation_buckets["preaccepted"]
            + citation_buckets["adjacent_extension"]
            + citation_buckets["expected_id"]
        )
        # Stricter golden-set view: source knowledge_id in expected set.
        expected_n = sum(
            1 for s in ask_sources if s.get("knowledge_id") in expected_ids
        )
        ask_citation_valid = valid_n == len(ask_sources) and len(ask_sources) > 0
        search_citation_valid = recall5  # search hit is locatable
        # Aggregate citation validity used in headline metric: expected_id fraction
        citation_valid_ratio_num = expected_n
        citation_valid_ratio_den = len(ask_sources)
    else:
        # No sources: valid only if search recalled and ask refused honestly,
        # or ask answered without sources (counts as invalid citation).
        ask_citation_valid = False
        search_citation_valid = recall5
        citation_valid_ratio_num = 0
        citation_valid_ratio_den = 0
        if not ans.strip() and recall5:
            # no_answer with search hit — citation N/A for ask; count as search ok
            ask_citation_valid = False

    # Hallucination: forbidden fact asserted in ask.answer only
    hallucination = ans_has_forbidden

    # E2E: search recall + ask fact correct + ask citation valid
    e2e_pass = recall5 and ask_fact_correct and ask_citation_valid and not hallucination

    # Legacy "facts_correct" / "grounded" kept for comparison scripts but now
    # based on ask.answer only (not mixed search/read text).
    facts_correct = ask_fact_correct
    citation_valid = ask_citation_valid
    grounded = ask_fact_correct and ask_citation_valid

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

    return {
        "top1_hit": top1_hit,
        "recall5": recall5,
        "facts_correct": facts_correct,
        "ask_fact_correct": ask_fact_correct,
        "citation_valid": citation_valid,
        "ask_citation_valid": ask_citation_valid,
        "search_citation_valid": search_citation_valid,
        "no_hallucination": not hallucination,
        "e2e_pass": e2e_pass,
        "read_has_facts": read_has_facts,
        "score": score,
        "top1_id": top1_id,
        "cand_ids": cand_ids,
        "ask_has_answer": bool(ans.strip()),
        "ask_source_count": len(ask_sources),
        "forbidden_violated": ans_has_forbidden,
        "citation_buckets": citation_buckets,
        "citation_valid_ratio_num": citation_valid_ratio_num,
        "citation_valid_ratio_den": citation_valid_ratio_den,
        "grounded": grounded,
    }


def score_no_answer(case, d):
    """Score a no-answer case per rubric 5.2."""
    forbidden = case.get("forbidden_facts", [])
    ans, srcs, raw_ev, snap = get_ask_answer(d)
    cands = get_search_candidates(d)
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
        "top1_id": (cands[0].get("knowledge_id") if cands else None),
        "cand_ids": [c.get("knowledge_id") for c in cands if c.get("knowledge_id")],
        "ask_has_answer": bool(ans.strip()),
        "ask_source_count": len(srcs) + len(raw_ev),
    }


def main():
    rows = []
    for case in GOLDEN:
        cid = case["case_id"]
        path = OUT / f"{cid}.json"
        if not path.exists():
            print(f"MISSING {cid}")
            continue
        d = load_case(cid)
        if case.get("expected_no_answer"):
            sc = score_no_answer(case, d)
            rows.append({"case_id": cid, "type": "no_answer", **sc})
        else:
            sc = score_answerable(case, d)
            rows.append({"case_id": cid, "type": "answerable", **sc})
    (OUT / "scored.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"scored {len(rows)} cases -> {OUT / 'scored.json'}")


if __name__ == "__main__":
    main()
