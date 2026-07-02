"""WikiPageLocator —— 按查询定位文件系统 wiki 页。

第二阶段 SizeAwareRouter 规模判定与 WikiReadStage 执行共享此定位器:
  - SizeAwareRouter 用 ``locate()`` 的命中页数判定规模档(wiki_read / blend / full_search)
  - WikiReadStage 用 ``locate()`` 的命中候选作 wiki_read 档的检索结果

与旧 ``db.search_wiki_fts``(查 SQLite ``wiki_pages`` 表)正交:本定位器扫描
``wiki/*.md`` 文件系统产物,不依赖数据库。

候选 dict 对齐 hybrid_search 检索候选 schema(id / text / metadata /
match_channels),便于 Task 1.3 blend 融合时与检索候选统一处理。wiki 候选
id 形如 ``wiki:<page_type>:<slug>``,与检索候选 ``page_id:block_id`` 不冲突。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import jieba

from src.services.wiki_index_compiler import PAGE_TYPE_DIRS
from src.services.wiki_slug import read_frontmatter
from src.utils.config import Config

logger = logging.getLogger(__name__)

# 路由信号词与高频虚词:这些是 SizeAwareRouter 的意图判定信号(哪些/所有/对比…)
# 或提问常用虚词(怎么/如何/是什么),不是检索词。在 wiki 命中计分前过滤掉,
# 避免虚高命中数污染规模判定 —— 否则"哪些营销通知"会因"哪些"刷高命中数误判档位。
_STOPWORDS = {
    "是", "的", "了", "在", "和", "与", "或", "及", "等", "都", "也", "就", "还",
    "把", "被", "给", "向", "到", "对", "为", "由", "从",
    "什么", "怎么", "如何", "为何", "为什么", "哪", "哪些", "哪个",
    "所有", "全部", "全", "每个", "各个",
    "对比", "比较", "列举", "列出", "罗列",
    "吗", "呢", "啊", "吧", "么", "哦", "哈",
    "一个", "这个", "那个", "这些", "那些",
    "请", "帮", "帮忙", "告诉", "说", "问",
}

_PUNCT_RE = re.compile(r"^[\s\W]+$")


class WikiPageLocator:
    """扫描 ``wiki/<page_type>/*.md``,按查询命中分返回候选页。

    Args:
        wiki_dir: wiki 根目录。``None`` 时从 ``Config`` 读取
            (``knowledge_workflow.wiki_dir``,默认 ``wiki``)。测试可注入临时目录,
            生产由 ``AppContainer`` 走默认值。
    """

    def __init__(self, wiki_dir: str | Path | None = None) -> None:
        if wiki_dir is not None:
            self._wiki_dir = Path(wiki_dir)
        else:
            self._wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))

    @property
    def wiki_dir(self) -> Path:
        return self._wiki_dir

    def locate(self, query: str, top_n: int = 10) -> tuple[list[dict], int]:
        """按 query 定位命中 wiki 页。

        Returns:
            ``(候选列表, 命中总数)``。候选按命中分降序并截断 ``top_n``;命中总数
            为排序前的全部命中数(不截断),供 SizeAwareRouter 判档。wiki 目录缺失
            或 query 为空时返回 ``([], 0)`` 且不抛异常。
        """
        query = (query or "").strip()
        if not query:
            return [], 0
        if not self._wiki_dir.exists():
            logger.warning("wiki 目录不存在,跳过定位: %s", self._wiki_dir)
            return [], 0

        tokens = _tokenize(query)
        if not tokens:
            return [], 0

        scored: list[dict] = []
        for ptype in PAGE_TYPE_DIRS:
            sub = self._wiki_dir / ptype
            if not sub.is_dir():
                continue
            for md in sorted(sub.glob("*.md")):
                cand = self._score_page(md, ptype, tokens)
                if cand is not None:
                    scored.append(cand)

        scored.sort(key=lambda c: c["metadata"]["wiki_hit_score"], reverse=True)
        return scored[:top_n], len(scored)

    @staticmethod
    def _score_page(path: Path, page_type: str, tokens: list[str]) -> dict | None:
        fm = read_frontmatter(path)
        title = str(fm.get("title") or path.stem)
        key_entities = fm.get("key_entities") or []
        if isinstance(key_entities, str):
            key_entities = [key_entities]
        body = _read_body(path)

        title_low = title.lower()
        body_low = body.lower()
        ke_low = " ".join(str(k) for k in key_entities).lower()

        score = 0.0
        for tok in tokens:
            t = tok.lower()
            if not t:
                continue
            if t in title_low:
                score += 3.0
            if t in ke_low:
                score += 2.0
            if t in body_low:
                score += 1.0
        if score <= 0:
            return None

        return {
            "id": f"wiki:{page_type}:{path.stem}",
            "text": body,
            "metadata": {
                "page_type": page_type,
                "title": title,
                "path": str(path),
                "knowledge_id": fm.get("knowledge_id"),
                "key_entities": list(key_entities),
                "source_hash": fm.get("source_hash"),
                "wiki_hit_score": float(score),
            },
            "match_channels": ["wiki_read"],
        }


def _tokenize(query: str) -> list[str]:
    """jieba 分词 + 停用词/标点过滤,保留专名与实词,去重保序。"""
    raw = [t.strip() for t in jieba.cut(query) if t.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for tok in raw:
        if tok in _STOPWORDS or _PUNCT_RE.match(tok):
            continue
        low = tok.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(tok)
    return out


def _read_body(path: Path) -> str:
    """读 markdown 正文(剥离 frontmatter ``---`` 块)。"""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()
