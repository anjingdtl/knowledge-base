"""real-hybrid eval 引擎 — 真 HybridSearcher(keywords 模式,零 embedding)。

OfflineIndex(BM25+bigram)不走 hybrid_search/jieba/synonyms,无法反映 W3 lexical
强化。本引擎把 evals/fixtures 索引进临时 DB(knowledge + blocks + FTS),用真
HybridSearcher(keywords 模式,零 embedding,确定性)跑查询,结果 schema 对齐
OfflineIndex,使 run_retrieval_eval 的 run_single_query / compute_* 指标全复用。

W3 lexical_zh(dict/synonym/language-weight)通过 config 注入 HybridSearcher,
故本引擎直接测 W3 强化的 lexical 通道。

诚实测量:在 retrieval_zh 上的 Recall@5 如实报(≥0.7 达标 S4;<0.7 如实记为
finding,defer 真实数据 reindex)。绝不刷数。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# keywords 模式:只走 lexical(FTS5+jieba+synonyms)通道,跳过向量(零 embedding)。
_HYBRID_CFG = {
    "rag": {
        "search_mode": "keywords",
        "lexical_zh": {"enabled": True},
        "parent_child": {"enabled": False},
    }
}


def _now() -> str:
    return datetime.now().isoformat()


class RealHybridIndex:
    """Drop-in 替代 OfflineIndex 的 real-hybrid 引擎。

    每个实例假设外部已准备好 Database 单例(测试前 ``Database._instance=None;
    Database.connect(...)`` 重置)。index_fixture 把一个 fixture 文件索引为一条
    knowledge + 若干 block + FTS 行;search 走 HybridSearcher.keywords 模式。
    """

    def __init__(self) -> None:
        # 确保 Database 模块已导入(HybridSearcher db=None 时内部用 Database 类)
        from src.services import db as _db_mod  # noqa: F401

    def index_fixture(self, path: Path, content: str) -> None:
        from src.services.db import Database

        kid = f"rh-{path.stem}"
        source_path = path.name  # 对齐 OfflineIndex 的 source_path = filename
        Database.insert_knowledge({
            "id": kid, "title": path.stem, "content": content,
            "source_type": "file", "source_path": source_path, "file_type": "md",
            "file_size": len(content), "content_hash": f"rh-{path.stem}",
            "file_created_at": "", "file_modified_at": "", "tags": "[]", "version": 1,
            "created_at": _now(), "updated_at": _now(),
        })
        blocks = []
        for i, chunk in enumerate(self._split(content)):
            blocks.append({
                "id": f"{kid}:b{i}", "parent_id": None, "page_id": kid,
                "content": chunk, "block_type": "section", "properties": "{}",
                "order_idx": i, "created_at": _now(), "updated_at": _now(),
            })
        if not blocks:
            return
        Database.insert_blocks(blocks)
        Database.insert_blocks_fts([
            {"id": b["id"], "page_id": b["page_id"],
             "content": b["content"], "block_type": b["block_type"]}
            for b in blocks
        ])

    @staticmethod
    def _split(content: str) -> list[str]:
        lines = [ln for ln in content.splitlines() if ln.strip()]
        if not lines:
            return [content]
        chunks: list[str] = []
        buf: list[str] = []
        for ln in lines:
            if ln.startswith("#") and buf:
                chunks.append("\n".join(buf))
                buf = [ln]
            else:
                buf.append(ln)
        if buf:
            chunks.append("\n".join(buf))
        return chunks or [content]

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        from src.services.hybrid_search import HybridSearcher

        searcher = HybridSearcher(db=None, block_store=None, config=_HYBRID_CFG)
        raw = searcher.search(queries=[query], top_k=top_k)
        return [self._shape(r) for r in raw]

    @staticmethod
    def _shape(r: dict) -> dict:
        """对齐 OfflineIndex.search 结果 schema。

        HybridSearcher 返回的 block 带 ``id``(block id 形如 ``<kid>:b<i>``)与
        ``metadata.block_id``;source_path 由 kid → ``rh-<stem>`` → ``<stem>.md`` 反推。
        """
        from src.services.db import Database

        block_id = r.get("id") or r.get("metadata", {}).get("block_id", "")
        kid = str(block_id).split(":b")[0] if block_id else ""
        source_path = ""
        if kid:
            try:
                item = Database.get_knowledge(kid)
                if item:
                    source_path = item.get("source_path", "") or f"{kid.replace('rh-', '')}.md"
            except Exception:
                source_path = kid.replace("rh-", "") + ".md"
        if not source_path and kid:
            source_path = kid.replace("rh-", "") + ".md"
        title = Path(source_path).stem if source_path else kid
        score = float(r.get("rrf_score") or r.get("score") or 0.0)
        return {
            "source_path": source_path,
            "title": title,
            "score": score,
            "metadata": {
                "source_path": source_path,
                "path": source_path,
                "knowledge_id": kid,
                "block_id": block_id,
            },
            "citation": {"path": source_path},
        }
