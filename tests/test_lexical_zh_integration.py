"""W3 S4 集成验收:真实 HybridSearcher + 词典 + 索引,中文 Recall@5 >= 0.7。

不走 evals/run_retrieval_eval.py 的 OfflineIndex(它不调 hybrid_search)。
直接测生产路径:加载专名词典 + 同义词 -> insert_blocks + insert_blocks_fts 建索引 ->
HybridSearcher.search 中文 query -> 断言 top-5 命中 golden source。

API 确认 (read db.py / hybrid_search.py / conftest.py):
- Database.connect(db_path) 设置 Database._instance 单例
- insert_blocks(blocks) 需要 id/page_id/content/block_type 等 (block_fts JOIN blocks)
- insert_blocks_fts(blocks) 需要 id/page_id/content (b["id"] not b["block_id"])
- HybridSearcher(db=Database, config=dict_config).search(queries=[str], top_k=5)
- search_mode 从 config rag.search_mode 读取
- 结果 dict: id=block_id, metadata.block_id
"""
import json

import pytest

from src.services.hybrid_search import HybridSearcher


# golden:5 个中文查询的期望命中 block(id 含关键词)
FIXTURE_BLOCKS = [
    {"id": "b1", "page_id": "p1", "content": "FTTR 是光纤到房间技术,千兆接入方案。",
     "block_type": "section"},
    {"id": "b2", "page_id": "p2", "content": "创智杯大赛评价指标包含创新性和实用性。",
     "block_type": "section"},
    {"id": "b3", "page_id": "p3", "content": "营销通知通过企业微信推送给目标用户。",
     "block_type": "section"},
    {"id": "b4", "page_id": "p4", "content": "APP 注册流程需要手机号验证。",
     "block_type": "section"},
    {"id": "b5", "page_id": "p5", "content": "BSS 业务支撑系统管理计费与客户数据。",
     "block_type": "section"},
]

# 专名词典:让 FTTR/创智杯/BSS 等不被切成单字
DICT_CONTENT = "FTTR 1000 nz\n创智杯 1000 nz\nBSS 1000 nz\n"
# 同义词:让"光纤"命中 FTTR 上下文
SYN_CONTENT = "FTTR 光纤到房间\n"


QUERIES_EXPECTED = [
    ("FTTR是什么", "b1"),
    ("创智杯评价指标", "b2"),
    ("营销通知", "b3"),
    ("APP注册", "b4"),
    ("BSS系统", "b5"),
]


def _recall_at_5(results, expected_block_id):
    """检查 expected_block_id 是否在 results[:5] 中。"""
    ids = [r.get("id") or (r.get("metadata") or {}).get("block_id") for r in results[:5]]
    return 1.0 if expected_block_id in ids else 0.0


def _now():
    from datetime import datetime
    return datetime.now().isoformat()


def _make_block_rows(fixture_blocks):
    """将 fixture blocks 转为 insert_blocks 需要的完整行格式。"""
    rows = []
    for b in fixture_blocks:
        rows.append({
            "id": b["id"],
            "parent_id": None,
            "page_id": b["page_id"],
            "content": b["content"],
            "block_type": b["block_type"],
            "properties": "{}",
            "order_idx": 0,
            "created_at": _now(),
            "updated_at": _now(),
        })
    return rows


def test_zh_recall_with_lexical_enhancements(tmp_path, monkeypatch):
    """加载词典+同义词后,中文 Recall@5 >= 0.7(S4)。"""
    from src.services.db import Database
    from src.utils import chinese_tokenizer

    # 准备临时词典/同义词文件
    dict_file = tmp_path / "dict.txt"
    dict_file.write_text(DICT_CONTENT, encoding="utf-8")
    syn_file = tmp_path / "syn.txt"
    syn_file.write_text(SYN_CONTENT, encoding="utf-8")

    # Config dict 模拟
    cfg = {
        "rag": {
            "search_mode": "keywords",  # 强制关键词模式,不走向量(无 mock embedding)
            "lexical_zh": {
                "enabled": True,
                "dict_path": str(dict_file),
                "synonym_path": str(syn_file),
            },
            "rrf_weight_keyword_zh": 0.7,
            "rrf_weight_keyword_en": 0.5,
            "rrf_k": 40,
            "rrf_weight_semantic": 0.4,
        }
    }

    # 重置词典加载 flag,确保本测试能重新加载
    monkeypatch.setattr(chinese_tokenizer, "_lexical_dict_loaded", False)

    # 真正加载 jieba 词典(不是 monkeypatch 掉),因为我们测的是真实分词路径
    # insert_blocks_fts -> tokenize_chinese_full -> _ensure_lexical_dict -> jieba.load_userdict

    # 初始化 Database
    Database._instance = None
    Database.connect(str(tmp_path / "test.db"))

    # 先插入 blocks 表 (search_blocks_fts JOIN blocks)
    Database.insert_blocks(_make_block_rows(FIXTURE_BLOCKS))

    # 再插入 block_fts (分词索引,会触发 _ensure_lexical_dict 加载专名词典)
    Database.insert_blocks_fts(FIXTURE_BLOCKS)

    # 验证词典已加载:FTTR 应被 jieba 识别为整词
    import jieba
    fttr_tokens = list(jieba.cut("FTTR技术"))
    assert "FTTR" in fttr_tokens, (
        f"Dict not loaded: jieba tokenized 'FTTR技术' as {fttr_tokens}, expected 'FTTR' as one token"
    )

    # 创建 HybridSearcher (keyword-only mode, no vector store needed)
    searcher = HybridSearcher(db=Database, block_store=None, config=cfg)

    hits = 0.0
    miss_details = []
    for query, expected in QUERIES_EXPECTED:
        results = searcher.search(queries=[query], top_k=5)
        recall = _recall_at_5(results, expected)
        if recall < 1.0:
            top_ids = [r.get("id") for r in results[:5]]
            miss_details.append(f"  query='{query}' expected={expected} got={top_ids}")
        hits += recall

    recall = hits / len(QUERIES_EXPECTED)
    if recall < 0.7:
        pytest.fail(
            f"S4 失败: Recall@5 = {recall} < 0.7\n"
            f"Misses:\n" + "\n".join(miss_details)
        )
