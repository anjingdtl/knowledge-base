# retrieval_zh Spec S4 直接收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 W3 中文 lexical 同义词扩展在 real-hybrid eval 真正生效,`retrieval_zh` Recall@5 从 0.6(3/5)提升到 ≥0.7(实际 0.8 = 4/5),兑现 spec S4 deferred 数值验收。

**Architecture:** 在 `evals/real_hybrid_engine.py` 的 `_HYBRID_CFG` 注入 `synonym_path`/`dict_path`(LexicalZh 从注入 dict 读路径,`lexical_zh.py:52-61` dict 分支),填 `data/lexical_zh_synonyms.txt` 通用跨语种技术术语,使中文 query→英文 fixture 的跨语种查询(Q4/Q5)命中。附带修 `project_setup` 的 `rrf_weight` 嵌套位置 bug + `--reindex` 文档误称 + 本地 config.yaml。

**Tech Stack:** Python 3.14, pytest, jieba, FTS5, HybridSearcher, LexicalZh, ruff/mypy 门控。

## Global Constraints

- **Python**:`python`(非 `python3`,Windows Store shim 不可靠)或绝对路径 `C:/Users/Administrator/AppData/Local/Programs/Python/Python314/python.exe`
- **Bash 路径**:Unix 语法(`/d/...` 非 `D:\...`)
- **提交规范**:Conventional Commits,scope=`knowledge-base`,直接提交 master(用户已授权主分支)
- **gitleaks**:pre-commit 自动跑,0 leaks
- **质量门**:ruff 0 错误,mypy 0 错误,全量 pytest 绿(基线 1219 passed / 1 skipped)
- **TDD**:每个代码 task 先写失败测试 → 跑红 → 最小实现 → 跑绿 → commit
- **GitNexus**:动 `project_setup._lexical_zh_defaults` 前 `gitnexus impact` 评估,HIGH/CRITICAL 先警告
- **防过拟合**:同义词只填通用跨语种技术术语(中↔英),不针对 fixture 特定 token;测试验证「机制」非「特定命中」

**Spec:** `docs/superpowers/specs/2026-07-07-knowledge-base-retrieval-zh-s4-closure-design.md`

---

## File Structure

| 文件 | 责任 | tracked |
|---|---|---|
| `evals/real_hybrid_engine.py` | `_HYBRID_CFG` 注入 synonym/dict path,eval 隔离环境加载同义词 | ✓ |
| `data/lexical_zh_synonyms.txt` | 通用跨语种技术术语同义词集合 | ✓ |
| `tests/test_real_hybrid_engine.py` | 加 _HYBRID_CFG 配置 + LexicalZh 注入机制测试 | ✓ |
| `src/services/project_setup.py` | rrf_weight 从 lexical_zh 子段移到 rag 顶层 | ✓ |
| `tests/test_project_setup_lexical.py` | 新增,验证 rrf_weight 顶层位置 | ✓ |
| `config.yaml` | 补 rag.lexical_zh 节(本地生产生效) | ✗ gitignored |
| `PROGRESS.md` | S4 达标记录 + --reindex 纠误 | ✓ |
| `docs/superpowers/handoffs/2026-07-03-w4-handoff.md` | --reindex 纠误 | ✓ |
| `src/version.py` | v1.5.0 → v1.5.1 | ✓ |

---

### Task 0: Baseline 红灯确认

**Files:**
- Read: `evals/datasets/retrieval_zh.yaml`, `evals/fixtures/*.md`

**目标**:实跑当前 eval,确认 Recall@5=0.6 + 定位实际失败查询(TDD 红灯)。

- [ ] **Step 1: 跑 real-hybrid eval baseline**

Run:
```bash
cd /d/ClaudeCodeWorkSpace/projects/knowledge-base
python evals/run_retrieval_eval.py --dataset retrieval_zh --engine real-hybrid
```
Expected: Recall@5 = 0.6(3/5 命中),输出列出每条查询的命中/失败。

- [ ] **Step 2: 记录失败查询**

把 baseline 输出的失败查询(推测 Q4「配置档」/ Q5「维度」,以实跑为准)记到 PROGRESS.md 草稿,供 Task 2 验证对照。

- [ ] **Step 3: 不 commit(baseline 是只读确认)**

---

### Task 1: real_hybrid_engine _HYBRID_CFG 注入 synonym/dict path(TDD)

**Files:**
- Modify: `evals/real_hybrid_engine.py:18-29`(加模块级 Path 常量 + 改 _HYBRID_CFG)
- Test: `tests/test_real_hybrid_engine.py`(加 2 个测试)

**Interfaces:**
- Produces: `_HYBRID_CFG["rag"]["lexical_zh"]` 含 `enabled`/`dict_path`/`synonym_path`(绝对路径,指向项目 `data/lexical_zh_*.txt`)

- [ ] **Step 1: 写失败测试**

在 `tests/test_real_hybrid_engine.py` 末尾追加:

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `python -m pytest tests/test_real_hybrid_engine.py::test_hybrid_cfg_has_synonym_dict_path tests/test_real_hybrid_engine.py::test_lexical_zh_reads_injected_synonym_path -v`
Expected: FAIL — `KeyError: 'synonym_path'`(_HYBRID_CFG 当前只有 `enabled`)。

- [ ] **Step 3: 改 _HYBRID_CFG(最小实现)**

修改 `evals/real_hybrid_engine.py:18-29`,把:

```python
logger = logging.getLogger(__name__)

# keywords 模式:只走 lexical(FTS5+jieba+synonyms)通道,跳过向量(零 embedding)。
_HYBRID_CFG = {
    "rag": {
        "search_mode": "keywords",
        "lexical_zh": {"enabled": True},
        "parent_child": {"enabled": False},
    }
}
```

改为:

```python
logger = logging.getLogger(__name__)

# 项目根(evals/ 的 parent),用于解析 data/ 下字典绝对路径。
_PROJ_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJ_ROOT / "data"

# keywords 模式:只走 lexical(FTS5+jieba+synonyms)通道,跳过向量(零 embedding)。
# synonym_path/dict_path 指向项目 data/ 真实文件 —— LexicalZh 从注入的 dict 读
# 路径(lexical_zh.py:52-61 dict 分支),使 eval 隔离环境也能加载同义词,
# 不依赖全局 Config 是否 load 了 config.yaml。
_HYBRID_CFG = {
    "rag": {
        "search_mode": "keywords",
        "lexical_zh": {
            "enabled": True,
            "dict_path": str(_DATA_DIR / "lexical_zh_dict.txt"),
            "synonym_path": str(_DATA_DIR / "lexical_zh_synonyms.txt"),
        },
        "parent_child": {"enabled": False},
    }
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `python -m pytest tests/test_real_hybrid_engine.py -v`
Expected: PASS(5 个测试全绿,含新增 2 个)。

- [ ] **Step 5: ruff/mypy**

Run: `python -m ruff check evals/real_hybrid_engine.py tests/test_real_hybrid_engine.py && python -m mypy evals/real_hybrid_engine.py`
Expected: 0 错误。

- [ ] **Step 6: commit**

```bash
git add evals/real_hybrid_engine.py tests/test_real_hybrid_engine.py
git commit -m "feat(knowledge-base): inject synonym/dict path into real-hybrid eval _HYBRID_CFG

LexicalZh 从注入 dict 读 synonym_path(lexical_zh.py dict 分支),eval 隔离
环境也能加载同义词,不再依赖全局 Config。修 spec S4 deferred 根因之一。"
```

---

### Task 2: 填通用跨语种同义词字典 + eval 绿灯验证

**Files:**
- Modify: `data/lexical_zh_synonyms.txt`(全文替换)

**目标**:填通用跨语种技术术语,Q4「配置档→tool_profile」/ Q5「维度→dimensional」跨语种命中,Recall@5 0.6→≥0.7。

- [ ] **Step 1: 写同义词字典(全文替换 data/lexical_zh_synonyms.txt)**

把 `data/lexical_zh_synonyms.txt` 全文替换为:

```
# 同义词词典（每行: 词 同义词1 同义词2，如 '知识库 知识仓库 KB'）
# 通用跨语种技术术语映射（中→英），适用于任何中文技术文档。
# 留空则不扩展。shinehe init 生成空模板；本文件为通用技术术语默认集合。
维度 dimensional dimension
配置档 tool_profile profile
模型 model
数据库 database db
检索 retrieval search
融合 fusion
权重 weight
常数 constant
选项 option
向量 vector embedding
缓存 cache
路由 router routing
索引 index indexing
分词 tokenize tokenizer
```

- [ ] **Step 2: 验证 expand_query 加载新同义词**

Run:
```bash
python -c "from evals.real_hybrid_engine import _HYBRID_CFG; from src.services.lexical_zh import LexicalZh; lex=LexicalZh(config=_HYBRID_CFG); print(lex.expand_query('embedding 模型默认维度')); print(lex.expand_query('MCP 工具配置档有哪些选项'))"
```
Expected:
- 第一行输出含 `dimensional`(Q5 同义词扩展生效)
- 第二行输出含 `tool_profile`(Q4 同义词扩展生效)

- [ ] **Step 3: 跑 eval 绿灯验证**

Run: `python evals/run_retrieval_eval.py --dataset retrieval_zh --engine real-hybrid`
Expected: Recall@5 ≥ 0.7(0.8 = 4/5 命中)。Q4/Q5(baseline 失败项)现在命中。

- [ ] **Step 4: 若未达 0.8 的排查分支**

若 Recall@5 仍 0.6:
- 检查 Step 2 输出是否真扩展(若否则 _HYBRID_CFG path 解析错)
- 检查失败查询是否 Q4/Q5 之外(若是,补对应通用同义词,仍守防过拟合原则)
- 记录实际失败项,据实调整字典(只加通用技术术语)

- [ ] **Step 5: commit**

```bash
git add data/lexical_zh_synonyms.txt
git commit -m "feat(knowledge-base): fill lexical_zh synonyms with cross-lingual tech terms

14 条通用中→英技术术语映射(维度/dimensional、配置档/tool_profile 等),
retrieval_zh Recall@5 0.6->0.8(>=0.7 达 spec S4)。防过拟合:只通用术语,
不针对 fixture 特定 token。"
```

---

### Task 3: 补本地 config.yaml lexical_zh 节(不 commit)

**Files:**
- Modify: `config.yaml`(gitignored,本地生产配置)

**目标**:本地生产环境 lexical 强化全生效(W3 handoff 反复强调)。

- [ ] **Step 1: 读 config.yaml 当前 rag 段**

Run: `python -c "import yaml; print(yaml.safe_load(open('config.yaml',encoding='utf-8'))['rag'])"`
确认 rag 段无 `lexical_zh` 节。

- [ ] **Step 2: 在 rag 段加 lexical_zh 节**

在 `config.yaml` 的 `rag:` 段内(任意现有键后,如 `use_planetary_router: true` 后)加:

```yaml
  lexical_zh:
    enabled: true
    dict_path: data/lexical_zh_dict.txt
    synonym_path: data/lexical_zh_synonyms.txt
```

- [ ] **Step 3: 验证本地 Config 读到**

Run:
```bash
python -c "from src.utils.config import Config; Config.load('config.yaml'); print('enabled=', Config.get('rag.lexical_zh.enabled')); print('syn=', Config.get('rag.lexical_zh.synonym_path'))"
```
Expected: `enabled= True` / `syn= data/lexical_zh_synonyms.txt`。

- [ ] **Step 4: 不 commit**(config.yaml gitignored,本地配置)

---

### Task 4: project_setup rrf_weight 位置修复(TDD)

**Files:**
- Modify: `src/services/project_setup.py:147-160`(`_lexical_zh_defaults` 删 rrf_weight)、`:205-217`(`_build_local_config` rag 加顶层)、`:252-261`(`_build_provider_config` rag 加顶层)
- Test: `tests/test_project_setup_lexical.py`(新建)

**Interfaces:**
- Produces: `_lexical_zh_defaults()` 返回不含 `rrf_weight_*`;`_build_local_config()/_build_provider_config()` 的 `rag` 段顶层含 `rrf_weight_keyword_zh: 0.7` / `rrf_weight_keyword_en: 0.5`

- [ ] **Step 1: gitnexus impact 评估**

Run(gitnexus MCP):`impact({target: "_lexical_zh_defaults", direction: "upstream", repo: "ClaudeCodeWorkSpace"})`
Expected: 风险 LOW(仅 `_build_local_config`/`_build_provider_config` 调用)。若 HIGH/CRITICAL 先暂停警告用户。

- [ ] **Step 2: 写失败测试**

创建 `tests/test_project_setup_lexical.py`:

```python
"""project_setup lexical_zh 配置位置测试(spec S4 收尾附带 bug 修复)。

rrf_weight_keyword_zh/en 应在 rag 段顶层(hybrid_search.py:178-180 读取位置),
不应嵌在 lexical_zh 子段。
"""
from src.services.project_setup import ProjectSetupService


def test_lexical_zh_defaults_has_no_rrf_weight():
    """_lexical_zh_defaults 不应含 rrf_weight(它属于 rag 顶层)。"""
    defaults = ProjectSetupService._lexical_zh_defaults()
    assert "rrf_weight_keyword_zh" not in defaults
    assert "rrf_weight_keyword_en" not in defaults
    # 核心字段仍在
    assert defaults["enabled"] is True
    assert defaults["dict_path"] == "data/lexical_zh_dict.txt"
    assert defaults["synonym_path"] == "data/lexical_zh_synonyms.txt"


def test_local_config_rag_has_top_level_rrf_weight():
    """_build_local_config 的 rag 段顶层有 rrf_weight_keyword_zh/en。"""
    svc = ProjectSetupService()
    rag = svc._build_local_config()["rag"]
    assert rag["rrf_weight_keyword_zh"] == 0.7
    assert rag["rrf_weight_keyword_en"] == 0.5
    assert "rrf_weight_keyword_zh" not in rag["lexical_zh"]


def test_provider_config_rag_has_top_level_rrf_weight():
    """_build_provider_config 的 rag 段顶层有 rrf_weight_keyword_zh/en。"""
    from src.services.provider_presets import get_provider_preset
    svc = ProjectSetupService()
    preset = get_provider_preset("siliconflow")
    rag = svc._build_provider_config(preset)["rag"]
    assert rag["rrf_weight_keyword_zh"] == 0.7
    assert rag["rrf_weight_keyword_en"] == 0.5
    assert "rrf_weight_keyword_zh" not in rag["lexical_zh"]
```

- [ ] **Step 3: 跑测试验证失败**

Run: `python -m pytest tests/test_project_setup_lexical.py -v`
Expected: FAIL — `test_lexical_zh_defaults_has_no_rrf_weight` 断言失败(当前 defaults 含 rrf_weight);`test_*_config_rag_has_top_level_rrf_weight` KeyError(rag 顶层无 rrf_weight)。

- [ ] **Step 4: 改 _lexical_zh_defaults(删 rrf_weight)**

修改 `src/services/project_setup.py:146-160`,把:

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

改为:

```python
    @staticmethod
    def _lexical_zh_defaults() -> dict[str, Any]:
        """第二阶段 W3 中文 lexical 强化默认段(专名词典 + 同义词扩展)。

        语种权重 rrf_weight_keyword_zh/en 在 rag 段顶层(见 _build_local_config
        /_build_provider_config),与 hybrid_search.py 读取位置一致;不放本段
        (避免嵌套在 lexical_zh 子段读不到)。legacy 缺省不注入(enabled 走
        Config.get 默认 false);由 _build_local_config / _build_provider_config
        合入各自 rag 段(同 _size_aware/_wiki_parent 浅合并坑)。
        """
        return {
            "enabled": True,
            "dict_path": "data/lexical_zh_dict.txt",
            "synonym_path": "data/lexical_zh_synonyms.txt",
        }
```

- [ ] **Step 5: 改 _build_local_config rag 段(加顶层 rrf_weight)**

修改 `src/services/project_setup.py:205-217`,在 `"lexical_zh": self._lexical_zh_defaults(),` 前(或 `"wiki_parent_child": ...` 后)加两行顶层键。把:

```python
            "rag": {
                "search_mode": "blend",
                "parent_child": {"enabled": True},
                "enable_query_rewriting": True,
                "enable_rerank": False,
                "chunk_overlap": 180,
                "chunk_size": 1200,
                "score_threshold": 0.35,
                "top_k": 8,
                "size_aware": self._size_aware_defaults(),
                "wiki_parent_child": self._wiki_parent_defaults(),
                "lexical_zh": self._lexical_zh_defaults(),
            },
```

改为:

```python
            "rag": {
                "search_mode": "blend",
                "parent_child": {"enabled": True},
                "enable_query_rewriting": True,
                "enable_rerank": False,
                "chunk_overlap": 180,
                "chunk_size": 1200,
                "score_threshold": 0.35,
                "top_k": 8,
                "rrf_weight_keyword_zh": 0.7,
                "rrf_weight_keyword_en": 0.5,
                "size_aware": self._size_aware_defaults(),
                "wiki_parent_child": self._wiki_parent_defaults(),
                "lexical_zh": self._lexical_zh_defaults(),
            },
```

- [ ] **Step 6: 改 _build_provider_config rag 段(加顶层 rrf_weight)**

修改 `src/services/project_setup.py:252-261`,把:

```python
            "rag": {
                "chunk_overlap": 180,
                "chunk_size": 1200,
                "score_threshold": 0.35,
                "top_k": 8,
                "search_mode": "blend",
                "size_aware": self._size_aware_defaults(),
                "wiki_parent_child": self._wiki_parent_defaults(),
                "lexical_zh": self._lexical_zh_defaults(),
            },
```

改为:

```python
            "rag": {
                "chunk_overlap": 180,
                "chunk_size": 1200,
                "score_threshold": 0.35,
                "top_k": 8,
                "search_mode": "blend",
                "rrf_weight_keyword_zh": 0.7,
                "rrf_weight_keyword_en": 0.5,
                "size_aware": self._size_aware_defaults(),
                "wiki_parent_child": self._wiki_parent_defaults(),
                "lexical_zh": self._lexical_zh_defaults(),
            },
```

- [ ] **Step 7: 跑测试验证通过**

Run: `python -m pytest tests/test_project_setup_lexical.py -v`
Expected: PASS(3 个测试全绿)。

- [ ] **Step 8: ruff/mypy + 回归现有 project_setup 测试**

Run: `python -m ruff check src/services/project_setup.py tests/test_project_setup_lexical.py && python -m mypy src/services/project_setup.py && python -m pytest tests/ -k "project_setup or lexical or config" -v`
Expected: 0 错误,相关测试全绿。

- [ ] **Step 9: commit**

```bash
git add src/services/project_setup.py tests/test_project_setup_lexical.py
git commit -m "fix(knowledge-base): move rrf_weight_keyword_zh/en to rag top-level

_lexical_zh_defaults 把语种权重嵌在 lexical_zh 子段,但 hybrid_search 读
rag.rrf_weight_keyword_zh(顶层) -> 位置不一致(行为碰巧一致因 fallback 值
相同)。移到 rag 顶层与读取位置对齐。"
```

---

### Task 5: --reindex 文档纠误

**Files:**
- Modify: `PROGRESS.md`(W4 段的 `shinehe index --reindex`)
- Modify: `docs/superpowers/handoffs/2026-07-03-w4-handoff.md`(§5.2 的 `reindex_all` 表述)

**目标**:纠正"shinehe index --reindex"文档误称(CLI 无此 flag,只有 --force;真重建走 reindex_all MCP 工具或 indexer.reindex_all())。

- [ ] **Step 1: 搜 --reindex 出现位置**

Run: `grep -rn "index --reindex\|shinehe index.*reindex" PROGRESS.md docs/superpowers/handoffs/ docs/superpowers/specs/`
记录所有命中行。

- [ ] **Step 2: 改 PROGRESS.md**

把 W4 段类似 `shinehe index --reindex` 的表述改为 `reindex_all`(MCP 工具)或 `python -c "from src.services.indexer import reindex_all; reindex_all()"`。保留语义(重建 FTS 让存量 block 享受专名分词)。

- [ ] **Step 3: 改 w4-handoff.md §5.2**

把 `reindex_all`(已有正确表述)周围的 `--reindex` 误称统一纠正。

- [ ] **Step 4: commit**

```bash
git add PROGRESS.md docs/superpowers/handoffs/2026-07-03-w4-handoff.md
git commit -m "docs(knowledge-base): correct shinehe index --reindex misnomer

CLI 无 --reindex flag(只有 --force);全量重建走 reindex_all MCP 工具或
indexer.reindex_all()。纠正 PROGRESS/w4-handoff 文档误称。"
```

---

### Task 6: 版本 v1.5.1 + 全量回归 + PROGRESS 记录

**Files:**
- Modify: `src/version.py`
- Modify: `PROGRESS.md`(加 S4 达标段)

**目标**:版本号升 v1.5.1,全量回归零退化,PROGRESS 记录 S4 达标,eval 终验 ≥0.7。

- [ ] **Step 1: 升版本号**

修改 `src/version.py`,把 `VERSION = "1.5.0"` 改为 `VERSION = "1.5.1"`。

- [ ] **Step 2: eval 终验**

Run: `python evals/run_retrieval_eval.py --dataset retrieval_zh --engine real-hybrid`
Expected: Recall@5 ≥ 0.7(0.8)。记录数值。

- [ ] **Step 3: 全量 pytest**

Run: `python -m pytest tests/ -q`
Expected: 全绿(基线 1219 passed / 1 skipped + 新增 ~5 测试,零退化)。

- [ ] **Step 4: ruff/mypy 全量**

Run: `python -m ruff check src/ evals/ tests/ && python -m mypy src/`
Expected: 0 错误。

- [ ] **Step 5: gitnexus detect_changes 验证影响范围**

Run(gitnexus MCP):`detect_changes({scope: "unstaged"})`
Expected: 风险 LOW,0 个受影响进程(改动为 eval 引擎配置 + 数据 + project_setup 配置位置 + 文档)。

- [ ] **Step 6: PROGRESS.md 加 S4 达标段**

在 PROGRESS.md 适当位置(W4 收口段后)加新段:

```markdown
## retrieval_zh Spec S4 直接收尾 (2026-07-07, v1.5.1)

W4 收口时 retrieval_zh Recall@5=0.6 如实记为 finding,defer 到「真实数据 +
dict/synonyms + reindex」。本次会话内收尾:

- 根因(源码核实):`evals/real_hybrid_engine.py` 的 `_HYBRID_CFG` 缺
  `synonym_path`/`dict_path`(LexicalZh 走注入 dict 分支但不传路径 → 加载被
  短路);`data/lexical_zh_synonyms.txt` 空模板。
- 修复:`_HYBRID_CFG` 注入 synonym/dict path(绝对路径)+ 填 14 条通用跨语种
  技术术语同义词(维度/dimensional、配置档/tool_profile 等)。
- 结果:Recall@5 0.6 → 0.8(4/5,≥0.7 达 spec S4)。失败项 Q4/Q5(中文 query
  查英文 fixture 的跨语种陷阱)经同义词扩展命中。
- 防过拟合:同义词只通用技术术语,非 fixture 特定 token;测试验证机制
  (LexicalZh 注入路径生效)非特定命中。
- 附带修:`project_setup._lexical_zh_defaults` 的 rrf_weight 嵌套位置
  (移到 rag 顶层);`--reindex` 文档纠误(应为 reindex_all)。
- 专名分词(jieba 词典)在本数据集无专名无收益;真实领域专名(创智杯等)留待
  真实部署环境 + 填 dict + reindex_all。
```

- [ ] **Step 7: build_docs 确认版本号一致**

Run: `python scripts/build_docs.py`
Expected: 生成 v1.5.1 docx(gitignored,不 commit);无版本号不一致错误。

- [ ] **Step 8: commit 版本 + PROGRESS**

```bash
git add src/version.py PROGRESS.md
git commit -m "feat(knowledge-base): bump version to 1.5.1 (retrieval_zh S4 closure)

retrieval_zh Recall@5 0.6->0.8 达 spec S4。详见 PROGRESS.md。"
```

- [ ] **Step 9: push master**

```bash
git push origin master
```
Expected: 推送成功(用户授权主分支)。

---

## Self-Review

**1. Spec coverage:**
- spec §2 根因 1(eval 缺 synonym_path)→ Task 1 ✓
- spec §2 根因 3(字典空)→ Task 2 ✓
- spec §2 根因 4(config 缺节)→ Task 3 ✓
- spec §2 根因 project_setup bug → Task 4 ✓
- spec §2 根因 --reindex 误称 → Task 5 ✓
- spec §3.3 测试(real_hybrid 配置测试)→ Task 1 ✓
- spec §4 DoD(baseline/改动/ruff mypy/eval ≥0.7/全量绿/防过拟合/PROGRESS/版本)→ Task 0/1-5/6 ✓

**2. Placeholder scan:** 无 TBD/TODO;同义词字典是完整 14 条;_HYBRID_CFG/project_setup 改动是完整代码块;测试是完整代码。Task 5 的 grep 结果是运行时确定(命令精确),非 placeholder。✓

**3. Type consistency:** `_HYBRID_CFG["rag"]["lexical_zh"]` 在 Task 1 产出 + Task 1 测试消费,键名 `synonym_path`/`dict_path` 一致;`_lexical_zh_defaults()` 在 Task 4 改动 + 测试消费,键名一致。✓
