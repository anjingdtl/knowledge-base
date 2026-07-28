"""Document family identity for version isolation (SPEC v3 §D).

Prefer stable metadata / document numbers over title-only normalization.
"""
from __future__ import annotations

import re
from typing import Any

# 中电信桂〔2026〕61号 / 市场〔2026〕8 号 / 中国电信〔2020〕162号
_DOC_NUM_RE = re.compile(
    r"([^\s〔(【\[\]〕)】]{0,24})"
    r"[〔(【\[]\s*((?:19|20)\d{2})\s*[〕)】\]]\s*"
    r"(\d+)\s*号?"
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_VERSION_ORDINAL_RE = re.compile(r"第[一二三四五六七八九十0-9]+版|[0-9]+版|修订版|修订")
_GENERIC_NOISE = re.compile(
    r"关于印发|的通知|转发|各市分公司|现予印发|请遵照执行|"
    r"中国电信广西公司|中国电信广西|中国电信|广西公司|号百|"
    r"中电信桂|市场部|市场|年版|年修订|年|号|第|版"
)

# Core regulation topic phrases that should group editions even when 文号 differs.
_TOPIC_PHRASES = [
    "技能竞赛管理办法",
    "差旅费管理办法",
    "翼支付业务管理办法",
    "涉诈涉骚扰电话号码入网渠道处置细则",
    "合同专用章管理办法",
    "商业秘密保护管理办法",
    "安全生产管理办法",
    "授权管理办法",
    "合规管理办法",
    "保密工作管理办法",
    "营收资金管理办法",
    "权益业务合作管理办法",
    "产品问需",
    "网络信息安全考核",
]


def extract_doc_number(blob: str) -> tuple[str, int] | None:
    """Return (issuer+number key without year, year) when a 文号 is present."""
    m = _DOC_NUM_RE.search(blob or "")
    if not m:
        return None
    issuer = re.sub(r"\s+", "", m.group(1) or "").strip("-—_")
    year = int(m.group(2))
    num = m.group(3)
    # Family key ignores year so 2023/2026 revisions of same regulation group.
    # Prefer regulation topic from surrounding title when issuer is weak.
    return (f"{issuer}|{num}", year)


def extract_version_year_from_blob(blob: str) -> int | None:
    m = _DOC_NUM_RE.search(blob or "")
    if m:
        return int(m.group(2))
    years = [int(y) for y in _YEAR_RE.findall(blob or "")]
    return max(years) if years else None


def extract_topic_phrase(title: str) -> str | None:
    """Return a known regulation topic phrase if present in title."""
    t = title or ""
    for phrase in _TOPIC_PHRASES:
        if phrase in t:
            return phrase
    # Fallback: capture “…管理办法/实施细则/处置细则”
    m = re.search(
        r"([\u4e00-\u9fff]{2,20}(?:管理办法|实施细则|处置细则|工作规范|业务规范))",
        t,
    )
    if m:
        return m.group(1)
    return None


def normalize_regulation_title(title: str) -> str:
    """Collapse years/doc-numbers so edition titles of the same regulation match."""
    phrase = extract_topic_phrase(title)
    if phrase:
        return phrase
    t = title or ""
    t = _DOC_NUM_RE.sub("", t)
    t = _YEAR_RE.sub("", t)
    t = _VERSION_ORDINAL_RE.sub("", t)
    t = _GENERIC_NOISE.sub("", t)
    t = re.sub(r"年版|年修订|年|号|第|版", "", t)
    t = re.sub(r"[-—_/\\()（）【】\[\]<>《》\s\d]+", "", t)
    return t.strip()


def assign_document_family(
    *,
    title: str = "",
    text: str = "",
    knowledge_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute document_family_id with auditable basis and confidence.

    Priority:
      1. explicit metadata.document_family_id / family_id
      2. known regulation topic phrase / title-normalized topic
      3. 文号 issuer + number stem (fallback)
      4. knowledge_id alone (singleton family)
    """
    meta = metadata or {}
    for key in ("document_family_id", "family_id", "regulation_family_id"):
        explicit = meta.get(key)
        if isinstance(explicit, str) and explicit.strip():
            return {
                "document_family_id": explicit.strip(),
                "family_confidence": 1.0,
                "family_basis": f"metadata:{key}",
                "version_year": _year_from_meta_or_blob(meta, title, text),
                "source_version": str(meta.get("source_version") or meta.get("version") or ""),
            }

    blob = f"{title}\n{text[:800]}"
    year = _year_from_meta_or_blob(meta, title, text)
    phrase = extract_topic_phrase(title) or extract_topic_phrase(text[:200])
    topic = phrase or normalize_regulation_title(title)
    doc = extract_doc_number(blob)

    if topic and len(topic) >= 4:
        # Prefer topic-level family so 2023/2026 editions group even when 文号 numbers differ.
        fid = f"topic:{topic}"
        conf = 0.95 if phrase else (0.85 if doc else 0.7)
        basis = "topic_phrase" if phrase else "title_normalized"
        if doc:
            basis = f"{basis}+doc_num:{doc[0]}"
        return {
            "document_family_id": fid,
            "family_confidence": conf,
            "family_basis": basis,
            "version_year": year,
            "source_version": str(year or meta.get("source_version") or ""),
        }

    if doc:
        issuer_key, doc_year = doc
        return {
            "document_family_id": f"doc:{issuer_key}",
            "family_confidence": 0.6,
            "family_basis": f"doc_number:{issuer_key}",
            "version_year": year or doc_year,
            "source_version": str(year or doc_year or ""),
        }

    kid = (knowledge_id or "").strip()
    return {
        "document_family_id": f"kid:{kid}" if kid else "unknown",
        "family_confidence": 0.2 if kid else 0.0,
        "family_basis": "knowledge_id_fallback",
        "version_year": year,
        "source_version": str(year or ""),
    }


def _year_from_meta_or_blob(meta: dict[str, Any], title: str, text: str) -> int | None:
    for key in ("effective_year", "version_year", "doc_year"):
        v = meta.get(key)
        if isinstance(v, int) and 1900 <= v <= 2100:
            return v
        if isinstance(v, str) and v.isdigit():
            y = int(v)
            if 1900 <= y <= 2100:
                return y
    return extract_version_year_from_blob(f"{title} {text[:400]}")
