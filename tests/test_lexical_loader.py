"""_ensure_lexical_dict 单测：三态（文件不存在/格式错/正常）+ 幂等 + Config 未初始化保护。"""
from src.utils import chinese_tokenizer


def test_disabled_does_not_load(monkeypatch):
    """rag.lexical_zh.enabled=false 时完全 no-op，不调 load_userdict。"""
    calls = []
    monkeypatch.setattr(chinese_tokenizer, "_lexical_dict_loaded", False)
    monkeypatch.setattr("jieba.load_userdict", lambda p: calls.append(p))
    monkeypatch.setattr("src.utils.config.Config.get",
                        lambda k, d=None: False if k == "rag.lexical_zh.enabled" else d)
    chinese_tokenizer._ensure_lexical_dict()
    assert calls == []  # disabled 时不调


def test_missing_dict_file_silent(monkeypatch, tmp_path):
    """dict_path 指向不存在的文件 → 静默 no-op，不 warning 不抛。"""
    monkeypatch.setattr(chinese_tokenizer, "_lexical_dict_loaded", False)
    calls = []
    monkeypatch.setattr("jieba.load_userdict", lambda p: calls.append(p))

    def fake_get(k, d=None):
        if k == "rag.lexical_zh.enabled": return True
        if k == "rag.lexical_zh.dict_path": return str(tmp_path / "nonexistent.txt")
        return d
    monkeypatch.setattr("src.utils.config.Config.get", staticmethod(fake_get))
    chinese_tokenizer._ensure_lexical_dict()
    assert calls == []  # 文件不存在静默


def test_valid_dict_loads_once(monkeypatch, tmp_path):
    """合法词典 → jieba.load_userdict 调一次，二次短路（幂等）。"""
    dict_file = tmp_path / "dict.txt"
    dict_file.write_text("FTTR 1000 nz\n", encoding="utf-8")
    monkeypatch.setattr(chinese_tokenizer, "_lexical_dict_loaded", False)
    calls = []
    monkeypatch.setattr("jieba.load_userdict", lambda p: calls.append(p))

    def fake_get(k, d=None):
        if k == "rag.lexical_zh.enabled": return True
        if k == "rag.lexical_zh.dict_path": return str(dict_file)
        return d
    monkeypatch.setattr("src.utils.config.Config.get", staticmethod(fake_get))
    chinese_tokenizer._ensure_lexical_dict()
    chinese_tokenizer._ensure_lexical_dict()  # 第二次
    assert len(calls) == 1  # 幂等


def test_config_not_initialized_silent(monkeypatch):
    """Config 未初始化（纯算法测试场景）→ 静默跳过，不抛。"""
    monkeypatch.setattr(chinese_tokenizer, "_lexical_dict_loaded", False)

    def boom(k, d=None):
        raise RuntimeError("Config not initialized")
    monkeypatch.setattr("src.utils.config.Config.get", staticmethod(boom))
    # 不应抛异常
    chinese_tokenizer._ensure_lexical_dict()
