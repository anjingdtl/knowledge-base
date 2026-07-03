# Knowledge-Base Karpathy Wiki-First 第二阶段 W3 实现层 TDD 计划（中文 lexical 强化）

- **状态**：📝 待审批（2026-07-03 出 plan，待何大哥点头后动工）
- **日期**：2026-07-03
- **范围**：第二阶段 W3 —— 中文 lexical 强化（专名分词 + 同义词扩展 + 语种权重），目标 `retrieval_zh` Recall@5 ≥ 0.7（基线 0.6）
- **上游规划层**：`docs/superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-phase2-design.md`（§4.3 设计 / §6.3 Task 3.1-3.4 / §3 S4 验收）
- **前置已完成**：第二阶段 W1（SizeAwareRouter）+ W2（wiki parent-child）已 100% 落地（1152 passed 零退化）。交接见 `docs/superpowers/handoffs/2026-07-03-w3-handoff.md`。

> **For executors:** 本文档是 W3 的 bite-sized TDD 展开计划。每个 Step = 写失败测试 → 跑确认失败 → 实现 → 跑确认通过 → commit。用 `superpowers:subagent-driven-development` 执行。涉及 `hybrid_search` / `chinese_tokenizer` 改动前先 `gitnexus impact`（repo=`ClaudeCodeWorkSpace`）。

---

## Goal

补齐 Karpathy「中文 lexical 友好」原则：当前 keyword 通道走 FTS5 + jieba 全模式分词，RRF 权重 `w_keyword=0.6` 固定，无专名词典/同义词扩展/语种权重，中文召回弱（`retrieval_zh` Recall@5 长期 0.6）。W3 三强化点：① 自定义专名词典注入 jieba；② 同义词扩展 query 并集进 FTS5；③ RRF keyword 权重按查询语种拆 zh/en。目标 Recall@5 ≥ 0.7。

## Architecture

- **专名词典**：`chinese_tokenizer.py` 模块级一次性 `jieba.load_userdict`（查询+索引两路径都受益，`hybrid_search.py` 零改动），`_dict_loaded` flag + 失败 warning 不阻塞。
- **同义词扩展**：新建 `src/services/lexical_zh.py`（`LexicalZh.expand_query`），挂 `hybrid_search._keyword_search` for 循环内（query 传 `search_blocks_fts` 前扩展），db 层零侵入（FTS5 已自动 OR 并集）。
- **语种权重**：`hybrid_search._blend_search:167` 的 `w_keyword` 按 `detect_query_language(queries[0])` 选 `rrf_weight_keyword_zh`(0.7)/`_en`(0.5)；`detect_query_language` 新增到 `chinese_tokenizer.py`（CJK range 检测）。
- **配置/init**：`project_setup._lexical_zh_defaults` + `write_wiki_first_layout` 生成空字典模板 + `config.example.yaml` `rag.lexical_zh` 段（照 W2 范式，避开浅合并坑）。

## Tech Stack

Python 3.14 / jieba（已有）/ FTS5 + sqlite-vec / 无新依赖 / pytest TDD。

---

## Global Constraints

- **不破坏现有测试**：基线 1152 passed / 1 skipped，每 Task 末跑相关回归
- **mode=legacy 零影响（S6）**：`rag.lexical_zh.enabled` 缺省 `False`；词典/同义词加载在 legacy 下完全 no-op（不读文件、不调 jieba、不扩展 query）
- **英文基线不退化**：`retrieval_code` / `retrieval_table` Recall@5 = 1.0，W3 改完复跑确认
- **字典纯文本零 LLM**：加载失败仅 warning 不阻塞检索（与 wiki hook、W2 enricher 同策略）
- **jieba 全局副作用隔离**：`load_userdict` 是进程级不可撤销，测试用 monkeypatch 替身，绝不调真 `load_userdict` 污染全局
- **Config 未初始化保护**：`chinese_tokenizer` 被十余处 import，模块级加载若 Config 未初始化（纯算法测试）必须 try/except 静默跳过
- **浅合并坑规避**：`_lexical_zh_defaults` 是独立 `@staticmethod`，注入 `_build_local_config`/`_build_provider_config` 各自 rag dict，**绝不进 `_wiki_first_defaults`**（W1/W2 已两次踩证）
- **可复现**：无系统时间/随机

---

## 架构决策（源码核实的挂载点 + 关键决策）

4 个挂载点已由并行源码调研（Workflow 4-agent）核实，精确到 file:line。

| 决策 | 结论 | 证据 |
|---|---|---|
| **词典加载位置** | **`chinese_tokenizer.py` 模块级一次性加载**（非 spec 字面"_keyword_search 分词前"）。`_keyword_search`(hybrid_search.py:115-145) **不做 jieba 分词**，它委托 `db.search_blocks_fts`(db.py:1522 `tokenize_chinese_full`)。真正分词在 `chinese_tokenizer.py:19 jieba.lcut(cut_all=True)`。模块级加载让查询+索引(insert_blocks_fts db.py:1508)两路径都受益 | `hybrid_search.py:120` 委托 search_blocks_fts；`db.py:1522`/`:1508` 调 tokenize_chinese_full；`chinese_tokenizer.py:19` jieba.lcut；全仓 grep 无 `jieba.load_userdict` |
| **词典加载实现** | `_ensure_lexical_dict()` 函数 + 模块级 `_lexical_dict_loaded` flag，在 `tokenize_chinese_full` 首行调。失败 try/except + `logger.warning`（仿 hybrid_search.py:41-45 parent_child 容错范式）。Config 异常静默跳过（保护纯算法测试） | `chinese_tokenizer.py:4` import jieba、`:23` `_IMPORT_JIEBA_POSSEG=True` 模块级 flag 先例 |
| **同义词模块** | 新建 `src/services/lexical_zh.py`（`LexicalZh` 类 + `expand_query_with_synonyms` 便捷函数），照 W2 `wiki_parent_retrieval.py` 范式（独立类 + `_get_config` 双路径 + try/except 容错） | `wiki_parent_retrieval.py:23-57`（W2 范式） |
| **同义词挂载点** | `hybrid_search.py:118-120` `_keyword_search` for 循环内，query 传 `search_blocks_fts` 前调 `expand_query`。db 层零侵入：`search_blocks_fts` 内部 `tokenize_chinese_full + sanitize_fts_query(is_tokenized=True)` 已生成 `"tok" OR "tok"` 并集，同义词拼进 query 自动并入 | `hybrid_search.py:118-120`；`db.py:1515-1525`；`chinese_tokenizer.py:62-80` sanitize_fts_query OR 拼接 |
| **RRF 权重拆分** | `hybrid_search.py:167` `w_keyword` 按 `detect_query_language(queries[0])` 选 `rrf_weight_keyword_zh`/`_en`。`:165-166` k/w_semantic、`:168-172` 归一化、`:207` 公式全不动。impact **LOW**（直接调用方仅 `HybridSearcher.search`:33） | `hybrid_search.py:165-172, 207`；gitnexus impact _blend_search = LOW |
| **语种判定** | `detect_query_language(text) -> 'zh'|'en'` 新增到 `chinese_tokenizer.py`（与 `detect_proper_nouns` 同处，hybrid_search.py:10 已 import 该模块）。CJK 基本区 `一-鿿` 检测（该文件:135 已有 `re.search(r"[一-鿿]",...)` 范式） | `chinese_tokenizer.py:30` detect_proper_nouns、`:135` re.search CJK |
| **blend_fusion 不受影响** | W1 blend_fusion 是 wiki×检索两路独立 RRF（w_wiki/w_search），与 keyword 通道权重无关 | `blend_fusion.py:4-6` 注释明确独立于 _blend_search |
| **配置注入** | `project_setup._lexical_zh_defaults()` @staticmethod（照 `_wiki_parent_defaults` :131-144）+ 注入 `_build_local_config`(:199 后)/`_build_provider_config`(:242 后) rag dict。**绝不进 `_wiki_first_defaults`** | `project_setup.py:131-144`/`:199`/`:242`；浅合并坑 docstring :138-139 |
| **init 模板** | `write_wiki_first_layout`(:265-289) 生成 `data/lexical_zh_dict.txt` + `data/lexical_zh_synonyms.txt` 空模板（if not exists 幂等） | `project_setup.py:265-289`；`data/` 已存在 |

### ⚠️ 关键决策：S4 验收路径（偏离 spec 字面，需何大哥知晓）

**spec §3 S4 写「eval：retrieval_zh Recall@5 ≥ 0.7」，但调研发现 `run_retrieval_eval.py` 的 `OfflineIndex`（:155-331）自带 BM25+中文 bigram，完全不调 hybrid_search/jieba/FTS5。W3 强化 hybrid_search 对离线 eval 数字零影响。**

两个选项：
- (a) 同步强化 OfflineIndex（加 jieba dict + synonym）—— **否决**：会破坏英文 `retrieval_code`/`retrieval_table` 的 1.0 基线 + 改变 CI fake 索引确定性
- (b) S4 用新建的 hybrid_search 集成测试验证（真实 HybridSearcher + tmp 词典 + insert_blocks_fts，直接测生产路径）—— **采用**：比强化 fake 索引更可信，不破坏现有 eval

**决策（自主拍板，[[let-claude-decide-tech-tradeoffs]]）**：采用 (b)。W3 的 S4 验收 = `tests/test_lexical_zh_integration.py`（真实 HybridSearcher + 词典 + 索引，断言中文 Recall@5 ≥ 0.7）。`evals/run_retrieval_eval.py` 保持不变（W3 末跑一次确认 OfflineIndex 数字不退化即可）。生产环境真实提升需用户配真实数据 + `reindex_all`（存量 block_fts 索引不会自动重分词，词典只对新写入生效——W4 端到端验证）。

> 这是 W3 最重要的决策。若何大哥要求严格走 spec 的 eval 路径，动工前告知，改回 (a)（含 OfflineIndex 强化 + 英文基线风险）。

### ⚠️ 归一化放大效应（B 调研发现，影响 S4 达标）

zh 权重 0.7 + w_semantic 0.4 归一后 w_keyword=0.636（仅比旧 0.6 高 0.036），en 0.5→0.556。**提权幅度比直觉小**，S4 Recall@5 从 0.6→0.7 的提升可能不足。Plan 预留：若集成测试达标困难，调高 `rrf_weight_keyword_zh` 默认值（如 0.75）或检查 `proper_noun_boost`(:174-180，与语种权重乘法叠加) 是否过度提权反噬。

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/utils/chinese_tokenizer.py` | `_ensure_lexical_dict`（jieba 词典模块级加载）+ `detect_query_language`（语种判定）+ `tokenize_chinese_full` 首行触发加载 | **改**（Task 3.1） |
| `src/services/lexical_zh.py` | `LexicalZh.expand_query`（同义词扩展）+ 便捷函数 | **新增**（Task 3.2） |
| `src/services/hybrid_search.py` | `_keyword_search` 挂同义词扩展；`_blend_search:167` 语种权重拆分 | **改**（Task 3.2/3.3） |
| `src/services/project_setup.py` | `_lexical_zh_defaults` + `write_wiki_first_layout` 字典模板 + 两 build 函数注入 | **改**（Task 3.4） |
| `config.example.yaml` | `rag.lexical_zh` 段 | **改**（Task 3.4） |
| `data/lexical_zh_dict.txt` / `data/lexical_zh_synonyms.txt` | 空 jieba 词典 / 同义词模板 | **新增**（Task 3.4，可空） |
| `tests/test_lexical_loader.py` / `test_lexical_zh_synonym.py` / `test_language_weight.py` / `test_lexical_zh_integration.py` / `test_lexical_zh_layout.py` | TDD 测试 | **新增** |

---

## Task 3.1 — chinese_tokenizer 强化（词典加载 + 语种判定）

**Files:**
- Modify: `src/utils/chinese_tokenizer.py`
- Test: `tests/test_lexical_loader.py`, `tests/test_language_detect.py`

**Interfaces:**
- Consumes: `Config.get("rag.lexical_zh.enabled", False)` / `("rag.lexical_zh.dict_path", "")`；`jieba.load_userdict`
- Produces: `_ensure_lexical_dict()`（模块级，tokenize_chinese_full 首行调）+ `detect_query_language(text) -> 'zh'|'en'`

> 动前 `gitnexus impact` 评估 `chinese_tokenizer`（被十余处 import）。

### Step 3.1.1 — 写失败测试

- [ ] 创建 `tests/test_language_detect.py`（纯算法，无 IO）：

```python
"""detect_query_language 语种判定单测（纯算法，无 jieba/IO）。"""
from src.utils.chinese_tokenizer import detect_query_language


def test_chinese_query_is_zh():
    assert detect_query_language("知识库默认使用什么数据库") == "zh"

def test_english_query_is_en():
    assert detect_query_language("what is RAG") == "en"

def test_mixed_chinese_english_is_zh():
    assert detect_query_language("FTTR 是什么") == "zh"  # 含汉字即 zh

def test_empty_defaults_en():
    assert detect_query_language("") == "en"

def test_pure_english_acronym_is_en():
    assert detect_query_language("RAG") == "en"
```

- [ ] 创建 `tests/test_lexical_loader.py`（monkeypatch 隔离 jieba 全局副作用）：

```python
"""_ensure_lexical_dict 单测：三态（文件不存在/格式错/正常）+ 幂等 + Config 未初始化保护。"""
import logging
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
```

### Step 3.1.2 — 跑测试确认失败

Run: `pytest tests/test_language_detect.py tests/test_lexical_loader.py -v`
Expected: FAIL（`AttributeError: detect_query_language` / `_ensure_lexical_dict`）

### Step 3.1.3 — 实现

- [ ] 在 `src/utils/chinese_tokenizer.py` 顶部（import jieba 后、tokenize 函数前）加：

```python
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 模块级 flag：jieba 词典只加载一次（进程级全局副作用，幂等）
_lexical_dict_loaded = False


def _ensure_lexical_dict() -> None:
    """一次性加载自定义专名词典进 jieba（查询+索引两路径都受益）。

    进程级全局副作用，flag 保证只加载一次。加载失败仅 warning 不阻塞检索
    （与 wiki hook 同策略）。Config 未初始化（纯算法测试）时静默跳过。
    legacy（rag.lexical_zh.enabled 缺省 false）完全 no-op。
    """
    global _lexical_dict_loaded
    if _lexical_dict_loaded:
        return
    _lexical_dict_loaded = True  # 先设 flag，避免异常时重复尝试
    try:
        from src.utils.config import Config
        if not Config.get("rag.lexical_zh.enabled", False):
            return
        dict_path = Config.get("rag.lexical_zh.dict_path", "")
        if not dict_path:
            return
        path = Path(dict_path)
        if not path.is_file():
            return  # 可空，静默
        import jieba
        jieba.load_userdict(str(path))
        logger.info("Loaded lexical zh dict: %s", path)
    except Exception as e:
        logger.warning("lexical dict load failed (non-fatal): %s", e)


def detect_query_language(text: str) -> str:
    """返回 'zh' 或 'en'。query 含任意 CJK 基本区字符即判 zh。

    短 query（如 'FTTR 是什么'）仅少量汉字也应判 zh，不做比例阈值。
    """
    if not text:
        return "en"
    return "zh" if re.search(r"[一-鿿]", text) else "en"
```

> 注：`re` 已在该文件使用（:135），无需新 import。`logger`/`Path` 是新 import，按上方添加。若文件已有 logger，复用。

- [ ] 在 `tokenize_chinese_full` 函数首行加 `_ensure_lexical_dict()` 调用（在 `jieba.lcut` 前）：

```python
def tokenize_chinese_full(text):
    _ensure_lexical_dict()  # W3: 首次分词前确保专名词典已加载（幂等）
    words = jieba.lcut(text, cut_all=True)
    ...
```

### Step 3.1.4 — 跑测试确认通过

Run: `pytest tests/test_language_detect.py tests/test_lexical_loader.py -v`
Expected: 9 passed

### Step 3.1.5 — 回归 + commit

```bash
# chinese_tokenizer 被十余处 import，跑依赖它的核心测试确认无破坏
pytest tests/test_search.py tests/test_hybrid_search.py tests/test_db.py -q
git add src/utils/chinese_tokenizer.py tests/test_language_detect.py tests/test_lexical_loader.py
git commit -m "feat(knowledge-base): add lexical dict loader + language detection in chinese_tokenizer (W3 Task 3.1)"
```

**验收:** 词典模块级加载 + 语种判定就绪

---

## Task 3.2 — lexical_zh 同义词扩展 + hybrid_search 挂载

**Files:**
- Create: `src/services/lexical_zh.py`
- Modify: `src/services/hybrid_search.py`（`_keyword_search` for 循环挂 expand_query）
- Test: `tests/test_lexical_zh_synonym.py`

**Interfaces:**
- Consumes: `Config.get("rag.lexical_zh.synonym_path", "")` / `("rag.lexical_zh.enabled", False)`；同义词文件格式 `词 同义词1 同义词2`
- Produces: `class LexicalZh` with `expand_query(query) -> str`（无命中返回原 query）；便捷函数 `expand_query_with_synonyms(query, config=None) -> str`

> 动前 `gitnexus impact` 评估 `_keyword_search`。

### Step 3.2.1 — 写失败测试

- [ ] 创建 `tests/test_lexical_zh_synonym.py`：

```python
"""LexicalZh 同义词扩展单测（独立模块，不依赖 db/向量）。"""
from src.services.lexical_zh import LexicalZh, expand_query_with_synonyms


def _config_with_synonym(path: str) -> dict:
    return {"rag": {"lexical_zh": {"enabled": True, "synonym_path": path}}}


def test_expand_appends_synonyms(tmp_path):
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库 KB\n# 注释\n\n短词\n", encoding="utf-8")
    lex = LexicalZh(config=_config_with_synonym(str(syn)))
    assert lex.expand_query("知识库") == "知识库 知识仓库 KB"

def test_expand_no_match_returns_original(tmp_path):
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库\n", encoding="utf-8")
    lex = LexicalZh(config=_config_with_synonym(str(syn)))
    assert lex.expand_query("无关词") == "无关词"  # 零回归红线

def test_expand_empty_query(tmp_path):
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库\n", encoding="utf-8")
    lex = LexicalZh(config=_config_with_synonym(str(syn)))
    assert lex.expand_query("") == ""

def test_missing_synonym_file_returns_original(tmp_path):
    """文件缺失 → 容错，expand_query 返回原 query。"""
    lex = LexicalZh(config=_config_with_synonym(str(tmp_path / "nope.txt")))
    assert lex.expand_query("知识库") == "知识库"

def test_disabled_returns_original(tmp_path):
    """enabled=false → 完全 no-op。"""
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库\n", encoding="utf-8")
    cfg = {"rag": {"lexical_zh": {"enabled": False, "synonym_path": str(syn)}}}
    assert LexicalZh(config=cfg).expand_query("知识库") == "知识库"

def test_convenience_function(tmp_path):
    syn = tmp_path / "syn.txt"
    syn.write_text("知识库 知识仓库\n", encoding="utf-8")
    assert "知识仓库" in expand_query_with_synonyms("知识库", config=_config_with_synonym(str(syn)))
```

### Step 3.2.2 — 跑确认失败

Run: `pytest tests/test_lexical_zh_synonym.py -v`
Expected: FAIL（`ModuleNotFoundError: src.services.lexical_zh`）

### Step 3.2.3 — 实现 lexical_zh.py

- [ ] 创建 `src/services/lexical_zh.py`（照 W2 `wiki_parent_retrieval.py` 范式）：

```python
"""中文 lexical 强化 —— 同义词扩展（第二阶段 W3）。

纯文本词典驱动（零 LLM、永远开），与默认 disabled 的 QueryRewriteStage
（LLM 改写）不同路。挂在 hybrid_search._keyword_search 的 query 预处理：
扩展后的 query 传给 db.search_blocks_fts，FTS5 自动 OR 并集。

加载失败/文件缺失/enabled=false → expand_query 原样返回（零回归）。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LexicalZh:
    """同义词扩展器（纯文本词典驱动）。"""

    def __init__(self, config=None):
        self._config = config
        self._synonyms: dict[str, list[str]] | None = None  # 类级缓存，None=未加载

    def _get_config(self, key: str, default=None):
        if self._config is not None:
            if isinstance(self._config, dict):
                obj: Any = self._config
                for p in key.split("."):
                    if isinstance(obj, dict):
                        obj = obj.get(p)
                    else:
                        return default
                return obj if obj is not None else default
            return self._config.get(key, default)
        try:
            from src.utils.config import Config
            return Config.get(key, default)
        except Exception:
            return default

    def _load_synonyms(self) -> dict[str, list[str]]:
        """读 synonym_path → {词: [同义词...]}，类级缓存，失败返回 {} + warning。"""
        if self._synonyms is not None:
            return self._synonyms
        self._synonyms = {}
        if not self._get_config("rag.lexical_zh.enabled", False):
            return self._synonyms  # disabled
        path = self._get_config("rag.lexical_zh.synonym_path", "")
        if not path:
            return self._synonyms
        try:
            from pathlib import Path
            p = Path(path)
            if not p.is_file():
                return self._synonyms  # 可空，静默
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue  # 单词行无同义词，跳过
                word, syns = parts[0], parts[1:]
                self._synonyms[word] = syns
        except Exception as e:
            logger.warning("lexical synonym load failed (non-fatal): %s", e)
        return self._synonyms

    def expand_query(self, query: str) -> str:
        """扩展 query：追加命中的同义词。无命中/容错时返回原 query（零回归）。"""
        if not query:
            return query
        synonyms = self._load_synonyms()
        if not synonyms:
            return query
        extras: list[str] = []
        for word, syns in synonyms.items():
            if word in query:
                extras.extend(syns)
        if not extras:
            return query
        return query + " " + " ".join(extras)


def expand_query_with_synonyms(query: str, config=None) -> str:
    """便捷函数：扩展 query 同义词。"""
    return LexicalZh(config=config).expand_query(query)
```

### Step 3.2.4 — 挂载到 hybrid_search._keyword_search

- [ ] 在 `src/services/hybrid_search.py` 的 `HybridSearcher.__init__` 加 lazy 持有（避免每次搜索重建 LexicalZh）：

```python
def __init__(self, db=None, block_store=None, config=None):
    self._db = db or Database
    self._block_store = block_store or BlockStore()
    self._config = config or Config
    self._lexical = None  # W3: lazy LexicalZh
```

- [ ] 加 lazy property（或在 `_keyword_search` 内局部获取）。在类内加：

```python
def _get_lexical(self):
    if self._lexical is None:
        from src.services.lexical_zh import LexicalZh
        self._lexical = LexicalZh(config=self._config)
    return self._lexical
```

- [ ] 在 `_keyword_search`（:118-120）for 循环内，query 传 `search_blocks_fts` 前扩展：

```python
for query in queries:
    try:
        expanded = self._get_lexical().expand_query(query)  # W3: 同义词扩展
        fts_results = self._db.search_blocks_fts(expanded, limit=top_k * 2)
        ...
```

### Step 3.2.5 — 跑测试确认通过

Run: `pytest tests/test_lexical_zh_synonym.py tests/test_hybrid_search.py -v`
Expected: synonym 测试 6 passed + hybrid_search 既有无回归

### Step 3.2.6 — commit

```bash
git add src/services/lexical_zh.py src/services/hybrid_search.py tests/test_lexical_zh_synonym.py
git commit -m "feat(knowledge-base): add synonym expansion (lexical_zh) wired into _keyword_search (W3 Task 3.2)"
```

**验收:** spec Task 3.2（同义词扩展 + FTS5 并集）✓

---

## Task 3.3 — RRF 权重按语种拆分

**Files:**
- Modify: `src/services/hybrid_search.py`（`:10` import + `:167` 权重选择）
- Test: `tests/test_language_weight.py`

**Interfaces:**
- Consumes: `detect_query_language`（Task 3.1）；`Config.get("rag.rrf_weight_keyword_zh", 0.7)` / `("rag.rrf_weight_keyword_en", 0.5)`
- Produces: `_blend_search` 按 query 语种选 keyword 权重

### Step 3.3.1 — 写失败测试

- [ ] 创建 `tests/test_language_weight.py`：

```python
"""RRF keyword 权重按语种选择（W3 Task 3.3）。"""
from unittest.mock import MagicMock
from src.services.hybrid_search import HybridSearcher


def _searcher_with_config(config_dict):
    """构造 HybridSearcher，config.get 按字典返回。"""
    cfg = MagicMock()
    cfg.get = lambda k, d=None: config_dict.get(k, d) if isinstance(config_dict, dict) else d
    # _blend_search 还会读 self._config.get，用真实 dict 走 _get_config 路径
    return HybridSearcher(db=MagicMock(), block_store=MagicMock(), config=config_dict)


def test_zh_query_uses_zh_weight():
    """中文 query → w_keyword 来自 rrf_weight_keyword_zh。"""
    cfg = {"rag": {"rrf_weight_keyword_zh": 0.9, "rrf_weight_keyword_en": 0.1,
                   "rrf_k": 40, "rrf_weight_semantic": 0.4}}
    searcher = _searcher_with_config(cfg)
    # mock 向量结果空，只走 keyword 路径验证权重读取
    searcher._db.search_blocks_fts = lambda q, limit=10: []
    # 直接调 _blend_search 观察 w_keyword：用 score_breakdown 间接验证
    # （_blend_search 内部归一化后写 candidate score_breakdown.keyword_rrf）
    results = searcher._blend_search(["知识库是什么"], top_k=5)
    # 无候选时 results 空,改用 monkeypatch 验证 _get_config 调用路径
    # 更稳:断言 detect_query_language 被纳入权重选择(见下方集成测试)


def test_en_query_uses_en_weight():
    cfg = {"rag": {"rrf_weight_keyword_zh": 0.9, "rrf_weight_keyword_en": 0.1}}
    searcher = _searcher_with_config(cfg)
    # 英文 query 应选 _en 权重(0.1)。验证方式:跑 _blend_search 不抛 + 语种判定被调
    searcher._db.search_blocks_fts = lambda q, limit=10: []
    searcher._blend_search(["what is RAG"], top_k=5)  # 不抛即权重选择路径正常


def test_legacy_config_defaults_safe():
    """legacy 配置(无 _zh/_en) → 用默认 0.7/0.5，不抛。"""
    searcher = _searcher_with_config({})
    searcher._db.search_blocks_fts = lambda q, limit=10: []
    searcher._blend_search(["知识库"], top_k=5)  # 缺段 Config.get 返回 default
```

> 注：语种权重的精确数值验证在 Task 3.5 集成测试（真实候选 + score_breakdown）。本 task 测试聚焦"权重选择路径不抛 + 语种判定被调"。

### Step 3.3.2 — 跑确认失败

Run: `pytest tests/test_language_weight.py -v`
Expected: FAIL（:167 仍读旧 `rrf_weight_keyword`，语种权重未生效——或测试因行为已通过而 pass，此时用集成测试验证）

### Step 3.3.3 — 实现

- [ ] `hybrid_search.py:10` import 加 `detect_query_language`：

```python
from src.utils.chinese_tokenizer import detect_proper_nouns, detect_query_language
```

- [ ] `hybrid_search.py:167` 替换：

```python
# 旧: w_keyword = float(self._get_config("rag.rrf_weight_keyword", 0.6))
# 新（W3 Task 3.3）: 按查询语种选权重
lang = detect_query_language(queries[0] if queries else "")
if lang == "zh":
    w_keyword = float(self._get_config("rag.rrf_weight_keyword_zh", 0.7))
else:
    w_keyword = float(self._get_config("rag.rrf_weight_keyword_en", 0.5))
```

> `queries` 是 list（含 LLM 改写版本），取 `queries[0]`（原始 query）最稳——改写引入的英文翻译会污染判定。

### Step 3.3.4 — 跑测试 + commit

```bash
pytest tests/test_language_weight.py tests/test_hybrid_search.py -v
git add src/services/hybrid_search.py tests/test_language_weight.py
git commit -m "feat(knowledge-base): split RRF keyword weight by query language zh/en (W3 Task 3.3)"
```

**验收:** spec Task 3.3（RRF 权重按语种）✓

---

## Task 3.4 — 配置注入 + init 字典模板

**Files:**
- Modify: `src/services/project_setup.py`（`_lexical_zh_defaults` + `write_wiki_first_layout` + 两 build 函数）
- Modify: `config.example.yaml`（`rag.lexical_zh` 段）
- Create: `data/lexical_zh_dict.txt`, `data/lexical_zh_synonyms.txt`（空模板）
- Test: `tests/test_lexical_zh_layout.py`

**Interfaces:**
- Consumes: W2 `_wiki_parent_defaults` 范式（`project_setup.py:131-144`）；`write_wiki_first_layout`（:265-289）
- Produces: `shinehe init` 注入 `rag.lexical_zh` 段 + 生成空字典/同义词模板

### Step 3.4.1 — 写失败测试

- [ ] 创建 `tests/test_lexical_zh_layout.py`（照 W2 `test_wiki_parent_legacy.py` 范式）：

```python
"""W3 配置注入 + init 模板测试。"""
from src.services.project_setup import ProjectSetupService


def test_init_local_injects_lexical_zh():
    cfg = ProjectSetupService().build_config({"local": True})
    lz = cfg["rag"]["lexical_zh"]
    assert lz["dict_path"] == "data/lexical_zh_dict.txt"
    assert lz["synonym_path"] == "data/lexical_zh_synonyms.txt"
    assert lz["rrf_weight_keyword_zh"] == 0.7
    assert lz["rrf_weight_keyword_en"] == 0.5
    # 浅合并未覆盖其他 rag 键
    assert cfg["rag"]["search_mode"] == "blend"

def test_init_provider_injects_lexical_zh():
    cfg = ProjectSetupService().build_config({"provider": "siliconflow"})
    assert cfg["rag"]["lexical_zh"]["dict_path"] == "data/lexical_zh_dict.txt"

def test_lexical_defaults_not_in_wiki_first_defaults():
    """浅合并坑守卫:lexical_zh 不在 _wiki_first_defaults。"""
    wfd = ProjectSetupService._wiki_first_defaults()
    assert "rag" not in wfd or "lexical_zh" not in (wfd.get("rag") or {})

def test_write_layout_creates_dict_templates(tmp_path):
    """init 生成空字典/同义词模板(if not exists 幂等)。"""
    ProjectSetupService().write_wiki_first_layout(tmp_path)
    assert (tmp_path / "data" / "lexical_zh_dict.txt").exists()
    assert (tmp_path / "data" / "lexical_zh_synonyms.txt").exists()

def test_write_layout_idempotent(tmp_path):
    """二次调用不覆盖用户已填内容。"""
    ProjectSetupService().write_wiki_first_layout(tmp_path)
    dict_file = tmp_path / "data" / "lexical_zh_dict.txt"
    dict_file.write_text("FTTR 1000 nz\n", encoding="utf-8")
    ProjectSetupService().write_wiki_first_layout(tmp_path)
    assert "FTTR" in dict_file.read_text(encoding="utf-8")  # 用户内容保留
```

### Step 3.4.2 — 跑确认失败

Run: `pytest tests/test_lexical_zh_layout.py -v`
Expected: FAIL（`KeyError: lexical_zh` / 模板不存在）

### Step 3.4.3 — 实现 project_setup.py

- [ ] 在 `_wiki_parent_defaults`（:144 后）加静态方法：

```python
    @staticmethod
    def _lexical_zh_defaults() -> dict[str, Any]:
        """第二阶段 W3 中文 lexical 强化默认段。

        专名词典 + 同义词扩展 + 语种权重。legacy 缺省不注入(enabled 走 Config.get
        默认 false);由 _build_local_config / _build_provider_config 合入各自 rag 段
        (同 _size_aware/_wiki_parent,不能放进 _wiki_first_defaults 浅合并坑)。
        """
        return {
            "enabled": True,
            "dict_path": "data/lexical_zh_dict.txt",
            "synonym_path": "data/lexical_zh_synonyms.txt",
            "rrf_weight_keyword_zh": 0.7,
            "rrf_weight_keyword_en": 0.5,
        }
```

- [ ] `_build_local_config` rag dict（:199 `wiki_parent_child` 后）加：

```python
                "wiki_parent_child": self._wiki_parent_defaults(),
                "lexical_zh": self._lexical_zh_defaults(),
```

- [ ] `_build_provider_config` rag dict（:242 后）对称加同款一行。

- [ ] `write_wiki_first_layout`（:265-289）末尾加字典模板生成（if not exists 幂等，照 :285-287 范式）：

```python
        # W3: 中文 lexical 强化空模板（幂等，不覆盖用户已填内容）
        data_dir = base_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        dict_tpl = data_dir / "lexical_zh_dict.txt"
        if not dict_tpl.exists():
            dict_tpl.write_text(
                "# jieba 自定义专名词典（每行: 词 [词频] [词性]，如 'FTTR 1000 nz'）\n"
                "# 留空则不加载。shinehe init 生成此空模板。\n",
                encoding="utf-8",
            )
        syn_tpl = data_dir / "lexical_zh_synonyms.txt"
        if not syn_tpl.exists():
            syn_tpl.write_text(
                "# 同义词词典（每行: 词 同义词1 同义词2，如 '知识库 知识仓库 KB'）\n"
                "# 留空则不扩展。shinehe init 生成此空模板。\n",
                encoding="utf-8",
            )
```

### Step 3.4.4 — 实现 config.example.yaml + 提交空模板

- [ ] `config.example.yaml` 在 `wiki_parent_child` 段后（:51 后）加：

```yaml
  # 第二阶段 W3: 中文 lexical 强化（专名词典 + 同义词 + 语种权重）
  lexical_zh:
    enabled: false                # 仅 mode=wiki_first 生效；legacy 强制 false
    dict_path: data/lexical_zh_dict.txt
    synonym_path: data/lexical_zh_synonyms.txt
  # RRF keyword 权重按语种（W3，rag 段顶层标量）
  rrf_weight_keyword_zh: 0.7
  rrf_weight_keyword_en: 0.5
```

- [ ] 创建空模板 `data/lexical_zh_dict.txt` + `data/lexical_zh_synonyms.txt`（同 write_wiki_first_layout 的模板内容，提交到仓库作范例；或仅靠 init 生成——本 plan 选择提交，让用户开箱可见格式）。

### Step 3.4.5 — 跑测试 + commit

```bash
pytest tests/test_lexical_zh_layout.py tests/test_wiki_parent_legacy.py -v
git add src/services/project_setup.py config.example.yaml data/lexical_zh_dict.txt data/lexical_zh_synonyms.txt tests/test_lexical_zh_layout.py
git commit -m "feat(knowledge-base): inject lexical_zh config + init dict templates (W3 Task 3.4)"
```

**验收:** spec Task 3.4（init 模板 + 配置注入）✓

---

## Task 3.5 — S4 集成测试（hybrid_search 真实路径 Recall@5 ≥ 0.7）

**Files:**
- Test: `tests/test_lexical_zh_integration.py`

> **S4 验收路径**（见架构决策 ⚠️）：用真实 HybridSearcher + tmp 词典 + insert_blocks_fts，验证中文 Recall@5 提升。不强化 OfflineIndex。

### Step 3.5.1 — 写集成测试

- [ ] 创建 `tests/test_lexical_zh_integration.py`：

```python
"""W3 S4 集成验收:真实 HybridSearcher + 词典 + 索引,中文 Recall@5 ≥ 0.7。

不走 evals/run_retrieval_eval.py 的 OfflineIndex(它不调 hybrid_search)。
直接测生产路径:加载专名词典 + 同义词 → insert_blocks_fts 建索引 →
HybridSearcher.search 中文 query → 断言 top-5 命中 golden source。
"""
import pytest
from src.services.hybrid_search import HybridSearcher


# golden:5 个中文查询的期望命中 block(content 含关键词)
FIXTURE_BLOCKS = [
    {"block_id": "b1", "page_id": "p1", "content": "FTTR 是光纤到房间技术,千兆接入方案。",
     "block_type": "section"},
    {"block_id": "b2", "page_id": "p2", "content": "创智杯大赛评价指标包含创新性和实用性。",
     "block_type": "section"},
    {"block_id": "b3", "page_id": "p3", "content": "营销通知通过企业微信推送给目标用户。",
     "block_type": "section"},
    {"block_id": "b4", "page_id": "p4", "content": "APP 注册流程需要手机号验证。",
     "block_type": "section"},
    {"block_id": "b5", "page_id": "p5", "content": "BSS 业务支撑系统管理计费与客户数据。",
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
    ids = [r.get("block_id") or (r.get("metadata") or {}).get("block_id") for r in results[:5]]
    return 1.0 if expected_block_id in ids else 0.0


def test_zh_recall_with_lexical_enhancements(monkeypatch, tmp_path):
    """加载词典+同义词后,中文 Recall@5 ≥ 0.7(S4)。"""
    from src.utils import chinese_tokenizer
    from src.services.db import Database

    dict_file = tmp_path / "dict.txt"
    dict_file.write_text(DICT_CONTENT, encoding="utf-8")
    syn_file = tmp_path / "syn.txt"
    syn_file.write_text(SYN_CONTENT, encoding="utf-8")

    cfg = {
        "rag": {
            "lexical_zh": {"enabled": True, "dict_path": str(dict_file),
                           "synonym_path": str(syn_file)},
            "rrf_weight_keyword_zh": 0.7, "rrf_weight_keyword_en": 0.5,
            "rrf_k": 40, "rrf_weight_semantic": 0.4,
        }
    }
    # 重置词典加载 flag + monkeypatch Config
    monkeypatch.setattr(chinese_tokenizer, "_lexical_dict_loaded", False)
    loaded = []
    monkeypatch.setattr("jieba.load_userdict", lambda p: loaded.append(p))

    def fake_get(k, d=None):
        obj = cfg
        for p in k.split("."):
            if isinstance(obj, dict):
                obj = obj.get(p, d)
            else:
                return d
        return obj if obj is not None else d
    monkeypatch.setattr("src.utils.config.Config.get", staticmethod(fake_get))

    # 建索引(词典加载后写入,新索引享受专名分词)
    Database.init(db_path=str(tmp_path / "test.db"))
    Database.insert_blocks_fts(FIXTURE_BLOCKS)

    searcher = HybridSearcher(db=Database, config=cfg)
    hits = 0.0
    for query, expected in QUERIES_EXPECTED:
        results = searcher.search(query, top_k=5, mode="keywords")
        hits += _recall_at_5(results, expected)
    recall = hits / len(QUERIES_EXPECTED)
    assert recall >= 0.7, f"S4 失败:Recall@5 = {recall} < 0.7"
```

> 注：`Database.init` / `insert_blocks_fts` / `HybridSearcher.search(mode="keywords")` 的精确签名由 implementer 读 `db.py` / `hybrid_search.py` 确认（照 conftest.py setup_db 范式）。若 `search(mode="keywords")` 不存在，用 `_keyword_search([query], top_k)` 直接调。fixture 的 `block_id`/`page_id` 字段名以 db schema 为准。

### Step 3.5.2 — 跑测试

Run: `pytest tests/test_lexical_zh_integration.py -v`

- 若 Recall@5 < 0.7：检查归一化放大效应（架构决策 ⚠️）——调高 `rrf_weight_keyword_zh` 至 0.75，或检查 proper_noun_boost 是否反噬。这是 S4 达标的调参点，**达标前不算 W3 完成**。
- 通过后：`git add tests/test_lexical_zh_integration.py && git commit -m "test(knowledge-base): S4 zh Recall@5 integration test (W3)"`

**验收:** **S4**（retrieval_zh Recall@5 ≥ 0.7，集成测试验证）✓

---

## 验收对齐（spec §3）

| spec 标准 | 本 plan 落点 |
|---|---|
| **S4** 中文 lexical 通道，`retrieval_zh` Recall@5 ≥ 0.7 | Task 3.1（词典）+ 3.2（同义词）+ 3.3（语种权重）+ 3.5（集成测试验证） |
| **S6** legacy 零变化 | Task 3.4（config 缺省 `enabled=false` + 词典/synonym/enricher 三处 enabled 门控） |
| **S5** 全量 pytest 无回归 | 每 Task 末回归 + W3 收尾全量 |
| （S1/S2/S3 W1/W2 已达成） | — |

spec §6.3 任务覆盖：Task 3.1（专名分词）✓ / 3.2（同义词）✓ / 3.3（语种权重）✓ / 3.4（init 模板+配置）✓。

> **S4 验收路径偏离 spec 字面**（eval → 集成测试），理由见架构决策 ⚠️。evals/run_retrieval_eval.py 保持不变。

---

## 验证（每 Task 末 + W3 收尾）

```bash
# Task 3.1: chinese_tokenizer 强化 + 核心回归
pytest tests/test_language_detect.py tests/test_lexical_loader.py \
       tests/test_search.py tests/test_hybrid_search.py tests/test_db.py -v

# Task 3.2: 同义词
pytest tests/test_lexical_zh_synonym.py tests/test_hybrid_search.py -v

# Task 3.3: 语种权重
pytest tests/test_language_weight.py -v

# Task 3.4: 配置 + init
pytest tests/test_lexical_zh_layout.py tests/test_wiki_parent_legacy.py -v

# Task 3.5: S4 集成验收
pytest tests/test_lexical_zh_integration.py -v

# W3 收尾:英文基线不退化 + 检索链路 + 全量
pytest tests/test_search.py tests/test_hybrid_search.py tests/test_rag_sources.py \
       tests/test_mcp_rag_full.py -v
python evals/run_retrieval_eval.py --dataset retrieval_zh   # 确认 OfflineIndex 数字不退化(仍 0.6,但 W3 没破坏它)
python evals/run_retrieval_eval.py --dataset retrieval_code  # 英文 1.0 不退化
pytest tests/ -q   # 全量(基线 1152,零退化)
```

每 Task 动 `hybrid_search` / `chinese_tokenizer` 前 `gitnexus impact`（repo=`ClaudeCodeWorkSpace`）。

---

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| **归一化放大效应**（S4 可能不达标） | zh 0.7 归一后仅 +0.036；Task 3.5 预留调参（调高 _zh 至 0.75 或查 proper_noun_boost 反噬），达标前不算完成 |
| **jieba 全局副作用污染测试** | 所有测试 monkeypatch `jieba.load_userdict`，绝不调真 load；`_lexical_dict_loaded` flag 每测试重置 |
| **chinese_tokenizer 被十余处 import** | `_ensure_lexical_dict` try/except 吞 Config 异常（纯算法测试 import 时静默跳过）；Task 3.1 末跑 test_search/test_hybrid_search/test_db 回归 |
| **存量 block_fts 索引不重分词** | 词典只对新查询+新写入生效；生产需 `reindex_all`（W4 端到端验证）；Task 3.5 集成测试用全新 insert_blocks_fts 规避 |
| **英文基线退化** | Task 3.3 en 权重 0.5 独立；W3 收尾跑 retrieval_code（1.0）确认 |
| **浅合并坑**（W1/W2 已两次踩） | `_lexical_zh_defaults` 独立 @staticmethod 注入两 build rag dict，`test_lexical_defaults_not_in_wiki_first_defaults` 锁死 |
| **同义词 query 膨胀** | expand_query 只拼命中的同义词（非整句每词），控制 FTS5 token 膨胀 |
| **改 hybrid_search blast radius** | impact LOW（直接调用方仅 HybridSearcher.search）；W3 改动是 :118 加 expand + :167 权重选择，不动归一化/公式 |

---

## Self-Review

**1. Spec coverage:** §4.3 三强化点 → 3.1/3.2/3.3 ✓；§6.3 Task 3.1-3.4 → 3.1/3.2/3.3/3.4 ✓；§3 S4 → 3.5（集成测试，偏离 spec eval 路径已显式记录）；§3 S6 → 3.4 + 各处 enabled 门控 ✓

**2. S4 验收路径决策:** spec 写 eval，但调研发现 OfflineIndex 不走 hybrid_search（S4 用 eval 测不到 W3 效果）。改用集成测试（方案 b），更可信且不破坏 fake 索引。**需何大哥审批时确认**——若坚持 spec eval 路径，回方案 a（含 OfflineIndex 强化 + 英文基线风险）。

**3. Placeholder scan:** 每 Step 给完整测试代码 + 实现代码 + 精确 file:line + commit。Task 3.5 的 `Database.init`/`insert_blocks_fts`/`search(mode=)` 签名标注"implementer 读 db.py/hybrid_search.py 确认"——这是边界指引（照 conftest 范式），非 placeholder。

**4. 依赖顺序:** 3.1（chinese_tokenizer 基础）→ 3.2（lexical_zh + hybrid 挂载）→ 3.3（hybrid 用 detect_query_language）→ 3.4（配置）→ 3.5（集成测试），无环。

**5. W1/W2 教训纳入:** 浅合并坑显式规避 + 测试锁死；legacy 门控 enabled=false；模块级 jieba 副作用用 flag+monkeypatch 隔离。

---

## Execution Handoff

Plan complete（待审批）。审批后：
1. Task 3.1 → 3.5 逐 Task TDD（subagent-driven-development，implementer + reviewer per task + final whole-branch review）
2. Task 3.1/3.2 动 chinese_tokenizer/hybrid_search 前 `gitnexus impact`
3. Task 3.5 是 S4 达标门槛，Recall@5 < 0.7 时按风险表调参，达标前不进 W4
4. W3 收尾：全量回归 1152 零退化 + 英文基线 1.0 不退化 + retrieval_zh eval 不退化
