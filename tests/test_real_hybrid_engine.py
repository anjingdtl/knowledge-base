"""real-hybrid eval 引擎测试(Phase2 W4 Task 4.2)。

验证引擎:(1) 跑通不抛;(2) 产 OfflineIndex 同构 schema(_result_paths 可识别);
(3) keywords 模式零 embedding 确定性;(4) 中文 keyword 查询命中正确 fixture(机制正确)。
"""
from __future__ import annotations

from pathlib import Path

from src.services.db import Database


def _reset_db(db_path):
    Database._instance = None
    Database.connect(str(db_path))


def test_real_hybrid_search_returns_offline_schema(tmp_path):
    """search 结果含 source_path / metadata.source_path,可被 _result_paths 识别。"""
    from evals.real_hybrid_engine import RealHybridIndex

    _reset_db(tmp_path / "rh.db")
    idx = RealHybridIndex()
    idx.index_fixture(Path("architecture.md"),
                      "# Architecture\nStorage: SQLite with WAL mode\n")
    results = idx.search("SQLite 数据库", top_k=10)
    assert isinstance(results, list)
    if results:
        r = results[0]
        assert "source_path" in r or r.get("metadata", {}).get("source_path")


def test_real_hybrid_matches_expected_fixture(tmp_path):
    """中文 keyword 查询命中正确 fixture(机制正确,非数值断言)。"""
    from evals.real_hybrid_engine import RealHybridIndex

    _reset_db(tmp_path / "rh.db")
    idx = RealHybridIndex()
    idx.index_fixture(Path("architecture.md"),
                      "# Architecture\n知识库默认使用 SQLite with WAL mode 数据库\n")
    idx.index_fixture(Path("distractor.md"), "完全无关的内容 blah blah\n")
    results = idx.search("知识库默认使用什么数据库", top_k=5)
    paths = [r.get("source_path") or r.get("metadata", {}).get("source_path", "")
             for r in results]
    assert "architecture.md" in paths


def test_real_hybrid_deterministic_across_runs(tmp_path):
    """同样输入两次跑(各自 fresh db),结果一致(零 embedding,确定性)。"""
    from evals.real_hybrid_engine import RealHybridIndex

    def run_on(db_path):
        _reset_db(db_path)
        idx = RealHybridIndex()
        idx.index_fixture(Path("a.md"), "RRF 融合常数 k=60\n")
        return [r.get("source_path") for r in idx.search("RRF 常数", top_k=5)]

    assert run_on(tmp_path / "rh1.db") == run_on(tmp_path / "rh2.db")


def test_hybrid_cfg_has_synonym_dict_path():
    """_HYBRID_CFG 必须带 synonym_path/dict_path,指向项目 data/ 真实文件,
    使 eval 隔离环境也能加载同义词(不依赖全局 Config)。"""
    from evals.real_hybrid_engine import _HYBRID_CFG

    lexical = _HYBRID_CFG["rag"]["lexical_zh"]
    assert lexical["enabled"] is True
    syn_path = Path(lexical["synonym_path"])
    dict_path = Path(lexical["dict_path"])
    assert syn_path.is_file(), f"synonym_path 不存在: {syn_path}"
    assert dict_path.is_file(), f"dict_path 不存在: {dict_path}"


def test_lexical_zh_reads_injected_synonym_path(tmp_path):
    """LexicalZh 从注入的 config dict 读 synonym_path 并加载(机制验证,
    用临时同义词文件,不依赖项目字典内容)。"""
    from evals.real_hybrid_engine import _HYBRID_CFG
    from src.services.lexical_zh import LexicalZh

    syn = tmp_path / "syn.txt"
    syn.write_text("测试词 test_term\n", encoding="utf-8")
    cfg = {"rag": {"lexical_zh": {
        "enabled": True,
        "synonym_path": str(syn),
        "dict_path": _HYBRID_CFG["rag"]["lexical_zh"]["dict_path"],
    }}}
    lex = LexicalZh(config=cfg)
    assert "test_term" in lex.expand_query("测试词")
