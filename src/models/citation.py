"""统一引用模型 — 结构化、可解释的引用信息"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CitationLocation:
    """引用定位信息 — 描述内容在原始文档中的位置。

    所有字段均为 best-effort，缺失字段为 None，不伪造。
    - PDF → page
    - Excel → sheet
    - PPTX → slide
    - Markdown / DOCX → heading_path + paragraph_index
    - 代码 → line_start / line_end
    """
    page: int | None = None
    sheet: str | None = None
    slide: int | None = None
    heading_path: list[str] = field(default_factory=list)
    paragraph_index: int | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass
class Citation:
    """结构化引用 — search 和 ask 共用。

    包含:
    - 文档定位: document / path / knowledge_id / block_id
    - 内容位置: location (CitationLocation)
    - 分数: score (最终) + score_breakdown (各阶段分数)
    - 匹配信息: match_channels + reason
    - 原文: text
    """
    document: str
    path: str
    knowledge_id: str
    block_id: str
    location: CitationLocation
    score: float
    score_breakdown: dict[str, float | None]
    match_channels: list[str]
    reason: str
    text: str

    def to_dict(self) -> dict:
        """序列化为字典，供 API/MCP 输出。"""
        return {
            "document": self.document,
            "path": self.path,
            "knowledge_id": self.knowledge_id,
            "block_id": self.block_id,
            "location": {
                "page": self.location.page,
                "sheet": self.location.sheet,
                "slide": self.location.slide,
                "heading_path": self.location.heading_path,
                "paragraph_index": self.location.paragraph_index,
                "line_start": self.location.line_start,
                "line_end": self.location.line_end,
            },
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "match_channels": self.match_channels,
            "reason": self.reason,
            "text": self.text,
        }
