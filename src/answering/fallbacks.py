"""Answer text fallbacks, conflict formatting, and generation context."""
from __future__ import annotations

from typing import Any


def format_conflict_answer(conflicts: list[dict[str, Any]], question: str) -> str:
    lines = [
        f"针对问题「{question}」，知识库中存在相互冲突的已验证结论，"
        "系统不会自动选择某一方作为唯一答案。主要分歧如下：",
        "",
    ]
    for i, c in enumerate(conflicts, 1):
        reasons = "、".join(c.get("reason_codes") or []) or "内容分歧"
        lines.append(f"### 分歧 {i}（{reasons}）")
        lines.append(f"- 观点 A（claim `{c.get('claim_a_id')}`）: {c.get('statement_a')}")
        lines.append(f"- 观点 B（claim `{c.get('claim_b_id')}`）: {c.get('statement_b')}")
        ea = c.get("evidence_a") or []
        eb = c.get("evidence_b") or []
        if ea:
            e0 = ea[0]
            lines.append(
                f"  - A 证据: knowledge={e0.get('knowledge_id')} block={e0.get('block_id')}"
            )
        if eb:
            e0 = eb[0]
            lines.append(
                f"  - B 证据: knowledge={e0.get('knowledge_id')} block={e0.get('block_id')}"
            )
        lines.append("")
    lines.append(
        "建议：对照双方原始 Evidence，结合来源更新时间与适用范围自行裁决；"
        "也可在 Authoring 模式提交冲突审阅。"
    )
    return "\n".join(lines)


def format_no_answer(question: str, *, freshness: bool = False) -> str:
    if freshness:
        return (
            f"未能确认问题「{question}」的最新可靠结论。"
            "相关 Claim 可能已过期，或缺少可追溯的最新原始证据。"
            "请补充/更新来源文档后重试，或改用更具体的文档定位查询。"
        )
    return (
        f"知识库中未找到可回答「{question}」的充分证据。"
        "未返回仅有 Wiki 页面而无原始 Evidence 的结论。"
    )


def build_generation_context(
    claim_rows: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
    *,
    conflicts: list[dict[str, Any]],
) -> str:
    parts: list[str] = []
    if conflicts:
        parts.append(
            "【系统指令】以下 Claim 存在冲突，不得输出单一确定结论，须并列披露。"
        )
    for i, c in enumerate(claim_rows, 1):
        ev_bits = []
        for ev in (c.get("evidence") or [])[:3]:
            ev_bits.append(
                f"kid={ev.get('knowledge_id')} bid={ev.get('block_id')} "
                f"stance={ev.get('stance', 'supports')}"
            )
        parts.append(
            f"【已验证 Claim {i} id={c.get('claim_id')}】\n"
            f"{c.get('text')}\n证据: {'; '.join(ev_bits) or '（无）'}"
        )
    for i, r in enumerate(raw_rows[:8], 1):
        parts.append(
            f"【原始证据 {i} kid={r.get('knowledge_id')} bid={r.get('block_id')}】\n"
            f"{(r.get('text') or '')[:800]}"
        )
    return "\n\n".join(parts) if parts else "（无检索上下文）"


def fallback_hybrid_text(
    question: str,
    claim_rows: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
) -> str:
    lines = [f"基于已验证知识与原始证据，关于「{question}」：", ""]
    for c in claim_rows[:5]:
        cid = c.get("claim_id") or ""
        lines.append(f"- {c.get('text')} 〔claim:{cid}〕")
        for ev in (c.get("evidence") or [])[:1]:
            lines.append(
                f"  证据 block={ev.get('block_id')} knowledge={ev.get('knowledge_id')}"
            )
    if raw_rows and len(claim_rows) < 3:
        lines.append("")
        lines.append("补充原始片段：")
        for r in raw_rows[:3]:
            lines.append(
                f"- [{r.get('title') or r.get('knowledge_id')}] "
                f"{(r.get('text') or '')[:200]}"
            )
    return "\n".join(lines)


def fallback_raw_text(question: str, raw_rows: list[dict[str, Any]]) -> str:
    lines = [
        f"基于原始文档检索（未使用可验证 Claim 结论），关于「{question}」：",
        "",
    ]
    for r in raw_rows[:5]:
        lines.append(
            f"- [{r.get('title') or r.get('knowledge_id')}] "
            f"{(r.get('text') or '')[:300]}"
        )
    return "\n".join(lines)
