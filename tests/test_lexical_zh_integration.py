"""W3 S4 机制验证:专名词典加载+分词变更+同义词扩展+语言检测。

本测试验证 lexical-enhancement 机制在真实 HybridSearcher+FTS 路径上真正生效:
1. 全局 Config 启用 lexical_zh → _ensure_lexical_dict 真正调用 jieba.load_userdict
2. 加载后 jieba 分词产生可观测差异(如"创智杯"从 创智/杯 变为 整词)
3. LexicalZh.expand_query 追加同义词,FTS 查询被扩展
4. detect_query_language 对中文 query 返回 "zh"

重要: Recall@5=1.0 在此小型 fixture 上是 FTS5+jieba 默认行为即可达到的结果,
不代表 spec 中 retrieval_zh 0.6→0.7 的数值提升(需要真实 retrieval_zh 数据集+重索引,
推迟到 W4 端到端验证)。本测试的职责是机制验证,不是数值 S4。
"""
import pytest


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

# 专名词典:创智杯 是关键鉴别词 — jieba 默认切成 创智/杯,词典加载后保持为整词
DICT_CONTENT = "创智杯 1000 nz\nFTTR 1000 nz\nBSS 1000 nz\n"
# 同义词:FTTR → 光纤到房间
SYN_CONTENT = "FTTR 光纤到房间\n"


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


def test_zh_lexical_mechanisms_engage(tmp_path, monkeypatch):
    """验证 lexical-zh 机制真正生效:词典加载→分词差异+同义词扩展+语言检测。"""
    from src.services.db import Database
    from src.utils import chinese_tokenizer
    from src.utils.config import Config

    # --- Fix 1: 设置全局 Config 使 _ensure_lexical_dict 真正加载 ---
    dict_file = tmp_path / "dict.txt"
    dict_file.write_text(DICT_CONTENT, encoding="utf-8")
    syn_file = tmp_path / "syn.txt"
    syn_file.write_text(SYN_CONTENT, encoding="utf-8")

    Config.set("rag.lexical_zh.enabled", True)
    Config.set("rag.lexical_zh.dict_path", str(dict_file))
    Config.set("rag.lexical_zh.synonym_path", str(syn_file))

    # 重置词典加载 flag + 录制 load_userdict 调用
    monkeypatch.setattr(chinese_tokenizer, "_lexical_dict_loaded", False)
    load_calls = []
    _real_load = chinese_tokenizer.jieba.load_userdict

    def _recording_load(path):
        load_calls.append(str(path))
        _real_load(path)

    monkeypatch.setattr(chinese_tokenizer.jieba, "load_userdict", _recording_load)

    # Local config dict for HybridSearcher (keyword mode)
    cfg = {
        "rag": {
            "search_mode": "keywords",
            "lexical_zh": {
                "enabled": True,
                "dict_path": str(dict_file),
                "synonym_path": str(syn_file),
            },
        }
    }

    # 初始化 Database + 插入 blocks + FTS 索引(触发 _ensure_lexical_dict)
    Database._instance = None
    Database.connect(str(tmp_path / "test.db"))
    Database.insert_blocks(_make_block_rows(FIXTURE_BLOCKS))
    Database.insert_blocks_fts(FIXTURE_BLOCKS)

    # --- 机制验证 1: jieba.load_userdict 确实被调用 ---
    assert len(load_calls) == 1, (
        f"Expected exactly 1 load_userdict call, got {len(load_calls)}: {load_calls}"
    )
    assert load_calls[0] == str(dict_file), (
        f"load_userdict called with wrong path: {load_calls[0]} != {dict_file}"
    )

    # --- 机制验证 2: 词典加载导致分词差异(鉴别性断言) ---
    # jieba 默认把"创智杯"切成 ['创智', '杯']; 加载词典后 jieba.cut 保持整词
    import jieba
    tokens_with_dict = list(jieba.cut("创智杯"))
    assert "创智杯" in tokens_with_dict, (
        f"Dict loaded but '创智杯' still split: {tokens_with_dict}. "
        "This proves load_userdict ran but the term wasn't recognized."
    )
    # 额外验证: tokenize_chinese_full (FTS 索引路径) 也产出整词
    from src.utils.chinese_tokenizer import tokenize_chinese_full
    fts_tokens = tokenize_chinese_full("创智杯大赛")
    assert "创智杯" in fts_tokens, (
        f"tokenize_chinese_full should produce '创智杯' after dict load: {fts_tokens}"
    )

    # --- 机制验证 3: 同义词扩展真正生效 ---
    from src.services.lexical_zh import LexicalZh
    lexical = LexicalZh(config=cfg)
    expanded = lexical.expand_query("FTTR是什么")
    assert expanded != "FTTR是什么", (
        f"Synonym expansion did nothing: '{expanded}' == original. "
        "FTTR should have '光纤到房间' appended."
    )
    assert "光纤到房间" in expanded, (
        f"Expected '光纤到房间' in expanded query: '{expanded}'"
    )

    # --- 机制验证 4: 语言检测对中文 query 返回 "zh" ---
    from src.utils.chinese_tokenizer import detect_query_language
    for q in ["FTTR是什么", "创智杯评价指标", "BSS系统"]:
        assert detect_query_language(q) == "zh", (
            f"detect_query_language('{q}') should return 'zh'"
        )
    assert detect_query_language("network routing") == "en"

    # --- Recall@5 验证(机制层面的端到端确认) ---
    from src.services.hybrid_search import HybridSearcher
    searcher = HybridSearcher(db=Database, block_store=None, config=cfg)

    queries_expected = [
        ("FTTR是什么", "b1"),
        ("创智杯评价指标", "b2"),
        ("营销通知", "b3"),
        ("APP注册", "b4"),
        ("BSS系统", "b5"),
    ]

    hits = 0.0
    miss_details = []
    for query, expected in queries_expected:
        results = searcher.search(queries=[query], top_k=5)
        recall = _recall_at_5(results, expected)
        if recall < 1.0:
            top_ids = [r.get("id") for r in results[:5]]
            miss_details.append(f"  query='{query}' expected={expected} got={top_ids}")
        hits += recall

    recall = hits / len(queries_expected)
    if recall < 0.7:
        pytest.fail(
            f"S4 失败: Recall@5 = {recall} < 0.7\n"
            f"Misses:\n" + "\n".join(miss_details)
        )
