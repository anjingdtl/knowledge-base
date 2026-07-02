# Knowledge-Base Karpathy Wiki-First 对齐 — W1 地基实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Phase 间 review(用户指定)。

**Goal:** 实现 W1 地基——`shinehe init` 生成 wiki-first 目录契约(`raw/wiki/schema/artifacts` + `AGENTS.md`),`build_config` 产出 `knowledge_workflow` 段与安全默认值,清理 `chroma_dir` legacy。

**Architecture:** 扩展现有 `ProjectSetupService`(不新建 scaffolder 文件):`build_config` 两个 builder 共享 `_wiki_first_defaults()` helper;新增 `write_wiki_first_layout()` + `AGENTS_MD_TEMPLATE`;`_handle_init` 调用新方法在项目目录创建四目录。`config.example.yaml` 收敛 5 项安全默认值并删 `chroma_dir`。Config 仍是纯 dict,无 schema 类改动。

**Tech Stack:** Python 3.14、argparse、PyYAML、pytest、sqlite-vec(本阶段不动)。

## Global Constraints

- **Python 解释器**:用 `python`(非 `python3`,Windows Store shim 不可靠);测试用 `python -m pytest ...`
- **代码风格**:4 空格缩进、`snake_case`;遵循现有文件风格
- **Config 访问**:纯 dict,`Config.get("a.b.c", default)` 点号嵌套;`Config.set("a.b", val)` 设置
- **CLI 注册**:argparse 扁平 subparsers + `handlers` dict(见 `src/cli.py:283-297`)
- **不破坏现有测试**:基线约 951 passed,W1 完成后不得回归
- **向后兼容**:老配置无 `knowledge_workflow` 段时,`Config.get("knowledge_workflow.mode", "legacy")` 缺省 `legacy`,行为不变
- **AGENTS.md 幂等**:`write_wiki_first_layout` 不覆盖已存在的 `schema/AGENTS.md`(尊重用户定制)
- **关联 spec**:`docs/superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-design.md` §6.1

## File Structure

| 文件 | 职责 | 本阶段动作 |
|---|---|---|
| `src/services/project_setup.py` | init 配置构建 + 写入 + 客户端配置 | **扩展**:加 `_wiki_first_defaults` / `write_wiki_first_layout` / `AGENTS_MD_TEMPLATE` / `WIKI_FIRST_DIRS`;改两个 builder |
| `src/cli.py` | argparse 入口 + `_handle_init` | **改**:`_handle_init` 调用 `write_wiki_first_layout` |
| `config.example.yaml` | 配置示例 | **改**:收敛 5 项默认值、删 `chroma_dir` |
| `tests/test_project_setup.py` | ProjectSetupService 单测 | **扩展**:加 `TestWikiFirstLayout` + `TestBuildConfig` 新方法 + config.example 一致性测试 |
| `tests/test_cli.py` | CLI 单测 | **扩展**:加 init e2e 测试 |

---

## Task 1: build_config 注入 wiki-first 默认段与安全默认值

**Files:**
- Modify: `src/services/project_setup.py:35-134`(`build_config` / `_build_local_config` / `_build_provider_config`)
- Test: `tests/test_project_setup.py`(class `TestBuildConfig`)

**Interfaces:**
- Produces: `ProjectSetupService._wiki_first_defaults() -> dict` —— 返回 `{"knowledge_workflow": {...}, "wiki": {...}}`,被两个 builder `config.update(...)` 合并。后续阶段读取这些键时用 `Config.get("knowledge_workflow.mode", "legacy")` / `Config.get("wiki.auto_publish", False)`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_project_setup.py` 的 `TestBuildConfig` 类内(在 `test_default_provider_is_siliconflow` 之后):

```python
    def test_local_config_has_knowledge_workflow(self):
        """local 模式生成 knowledge_workflow 段,默认 wiki_first"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})
        kw = config["knowledge_workflow"]
        assert kw["mode"] == "wiki_first"
        assert kw["raw_dir"] == "raw"
        assert kw["wiki_dir"] == "wiki"
        assert kw["schema_file"] == "schema/AGENTS.md"
        assert kw["maintain_index_md"] is True
        assert kw["maintain_log_md"] is True

    def test_local_config_wiki_safe_defaults(self):
        """wiki 安全默认:auto_publish=False, lint_contradictions=True"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})
        assert config["wiki"]["auto_publish"] is False
        assert config["wiki"]["lint_contradictions"] is True
        assert config["wiki"]["enabled"] is True
        assert config["wiki"]["auto_compile"] is True

    def test_local_config_mcp_exposes_wiki_tools(self):
        """local mcp 收敛:experimental_tools_enabled=True"""
        service = ProjectSetupService()
        config = service.build_config({"local": True})
        assert config["mcp"]["experimental_tools_enabled"] is True
        assert config["mcp"]["allow_http_write"] is False

    def test_provider_config_has_knowledge_workflow(self):
        """provider 模式同样生成 wiki-first 段"""
        service = ProjectSetupService()
        config = service.build_config({"provider": "siliconflow"})
        assert config["knowledge_workflow"]["mode"] == "wiki_first"
        assert config["wiki"]["auto_publish"] is False

    def test_provider_config_mcp_safe_defaults(self):
        """provider mcp 收敛:write_policy=local_confirm"""
        service = ProjectSetupService()
        config = service.build_config({"provider": "siliconflow"})
        assert config["mcp"]["tool_profile"] == "extended"
        assert config["mcp"]["experimental_tools_enabled"] is True
        assert config["mcp"]["write_policy"] == "local_confirm"
        assert config["mcp"]["allow_http_write"] is False

    def test_wiki_first_defaults_helper_structure(self):
        """_wiki_first_defaults 返回 knowledge_workflow + wiki 两段"""
        service = ProjectSetupService()
        defaults = service._wiki_first_defaults()
        assert set(defaults.keys()) == {"knowledge_workflow", "wiki"}
        assert defaults["knowledge_workflow"]["mode"] == "wiki_first"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_project_setup.py::TestBuildConfig -v`
Expected: 6 个新测试 FAIL(`KeyError: 'knowledge_workflow'` 或 `AttributeError: _wiki_first_defaults`),原有 8 个测试 PASS。

- [ ] **Step 3: 实现 —— 加 helper + 改两个 builder**

在 `src/services/project_setup.py` 的 `class ProjectSetupService` 内,**`build_config` 方法之前**(约 line 34 `# 配置构建` 注释块下)插入:

```python
    @staticmethod
    def _wiki_first_defaults() -> dict[str, Any]:
        """wiki-first 模式的公共默认段:knowledge_workflow + wiki 安全默认。

        被 _build_local_config 与 _build_provider_config 共享,保证两种 init
        路径生成一致的 wiki-first 配置。后续阶段通过
        Config.get("knowledge_workflow.mode", "legacy") 读取。
        """
        return {
            "knowledge_workflow": {
                "mode": "wiki_first",
                "raw_dir": "raw",
                "wiki_dir": "wiki",
                "schema_file": "schema/AGENTS.md",
                "source_summary_dir": "wiki/sources",
                "entity_dir": "wiki/entities",
                "concept_dir": "wiki/concepts",
                "synthesis_dir": "wiki/syntheses",
                "comparison_dir": "wiki/comparisons",
                "maintain_index_md": True,
                "maintain_log_md": True,
            },
            "wiki": {
                "enabled": True,
                "auto_compile": True,
                "auto_link": True,
                "auto_publish": False,           # 收敛:review gate
                "lint_contradictions": True,     # 收敛:启用 lint 闭环
                "max_llm_calls_per_ingest": 3,
                "query_save_min_length": 100,
            },
        }
```

改 `_build_local_config`(原 line 55-95):在 `mcp` 段加两键,在 `return config` 前合并默认段。完整替换为:

```python
    def _build_local_config(self) -> dict[str, Any]:
        """构建本地模式配置（Ollama + 离线优先）"""
        preset = get_provider_preset("ollama")
        config: dict[str, Any] = {
            "embedding": {
                "base_url": preset.embedding_base_url,
                "model": preset.embedding_model,
                "provider": preset.canonical_name,
                "reuse_llm": True,
            },
            "llm": {
                "base_url": preset.llm_base_url,
                "model": preset.llm_model,
                "provider": preset.canonical_name,
                "temperature": 0.7,
                "max_tokens": 2048,
            },
            "mcp": {
                "tool_profile": "extended",
                "write_policy": "disabled",
                "experimental_tools_enabled": True,
                "allow_http_write": False,
            },
            "rag": {
                "search_mode": "blend",
                "parent_child": {"enabled": True},
                "enable_query_rewriting": True,
                "enable_rerank": False,
                "chunk_overlap": 180,
                "chunk_size": 1200,
                "score_threshold": 0.35,
                "top_k": 8,
            },
            "reranker": {
                "provider": "disabled",
                "enabled": False,
            },
            "storage": {
                "data_dir": "data",
                "db_name": "kb.db",
            },
        }
        config.update(self._wiki_first_defaults())
        return config
```

改 `_build_provider_config`(原 line 97-134):加 `mcp` 段、在末尾 `return` 前合并。完整替换为:

```python
    def _build_provider_config(self, preset: ProviderPreset) -> dict[str, Any]:
        """构建基于指定服务商的配置"""
        config: dict[str, Any] = {
            "embedding": {
                "base_url": preset.embedding_base_url,
                "model": preset.embedding_model,
                "provider": preset.canonical_name,
                "reuse_llm": True,
            },
            "llm": {
                "base_url": preset.llm_base_url,
                "model": preset.llm_model,
                "provider": preset.canonical_name,
                "temperature": 0.7,
                "max_tokens": 2048,
            },
            "mcp": {
                "tool_profile": "extended",
                "experimental_tools_enabled": True,
                "write_policy": "local_confirm",
                "allow_http_write": False,
            },
            "rag": {
                "chunk_overlap": 180,
                "chunk_size": 1200,
                "score_threshold": 0.35,
                "top_k": 8,
                "search_mode": "blend",
            },
            "storage": {
                "data_dir": "data",
                "db_name": "kb.db",
            },
        }

        if preset.reranker_base_url:
            config["reranker"] = {
                "base_url": preset.reranker_base_url,
                "model": preset.reranker_model,
                "enabled": True,
                "provider": preset.canonical_name,
            }

        config.update(self._wiki_first_defaults())
        return config
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_project_setup.py::TestBuildConfig -v`
Expected: 全部 PASS(原 8 + 新 6 = 14)。

- [ ] **Step 5: 提交**

```bash
git add src/services/project_setup.py tests/test_project_setup.py
git commit -m "feat(knowledge-base): build_config 注入 wiki-first 默认段与安全默认值"
```

---

## Task 2: write_wiki_first_layout 生成目录契约 + AGENTS.md

**Files:**
- Modify: `src/services/project_setup.py`(新增模块级常量 + `ProjectSetupService.write_wiki_first_layout` 方法)
- Test: `tests/test_project_setup.py`(新增 `TestWikiFirstLayout` 类)

**Interfaces:**
- Produces:
  - 常量 `AGENTS_MD_TEMPLATE: str` —— schema 模板(spec §7)
  - 常量 `WIKI_FIRST_DIRS: tuple[str, ...]` —— 8 个目录相对路径
  - `ProjectSetupService.write_wiki_first_layout(base_dir: Path) -> list[Path]` —— 创建目录 + AGENTS.md,返回创建的目录列表;幂等,不覆盖已有 AGENTS.md

- [ ] **Step 1: 写失败测试**

在 `tests/test_project_setup.py` 的 `TestWriteConfig` 类**之后**追加新类:

```python
# ---------------------------------------------------------------------------
# write_wiki_first_layout 测试
# ---------------------------------------------------------------------------


class TestWikiFirstLayout:
    """测试 wiki-first 目录契约生成"""

    def test_creates_all_directories(self, tmp_path):
        """创建全部 8 个目录"""
        from src.services.project_setup import WIKI_FIRST_DIRS

        service = ProjectSetupService()
        created = service.write_wiki_first_layout(tmp_path)

        rel = {p.relative_to(tmp_path).as_posix() for p in created}
        assert rel == set(WIKI_FIRST_DIRS)
        for rel_dir in WIKI_FIRST_DIRS:
            assert (tmp_path / rel_dir).is_dir()

    def test_creates_agents_md(self, tmp_path):
        """生成 schema/AGENTS.md 模板"""
        service = ProjectSetupService()
        service.write_wiki_first_layout(tmp_path)

        agents_md = tmp_path / "schema" / "AGENTS.md"
        assert agents_md.exists()
        content = agents_md.read_text(encoding="utf-8")
        assert "Source of truth" in content
        assert "raw/" in content
        assert "Page types" in content
        assert "Ingest workflow" in content

    def test_idempotent(self, tmp_path):
        """重复调用不抛错"""
        service = ProjectSetupService()
        service.write_wiki_first_layout(tmp_path)
        service.write_wiki_first_layout(tmp_path)  # 不抛异常
        assert (tmp_path / "schema" / "AGENTS.md").exists()

    def test_preserves_custom_agents_md(self, tmp_path):
        """已存在的 AGENTS.md 不被覆盖"""
        service = ProjectSetupService()
        (tmp_path / "schema").mkdir(parents=True)
        custom = "# My Custom AGENTS Rules\n"
        (tmp_path / "schema" / "AGENTS.md").write_text(custom, encoding="utf-8")

        service.write_wiki_first_layout(tmp_path)

        assert (tmp_path / "schema" / "AGENTS.md").read_text(encoding="utf-8") == custom

    def test_returns_created_dir_list(self, tmp_path):
        """返回值是创建的目录路径列表"""
        service = ProjectSetupService()
        created = service.write_wiki_first_layout(tmp_path)
        assert len(created) == 8
        assert all(p.is_dir() for p in created)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_project_setup.py::TestWikiFirstLayout -v`
Expected: 5 个测试 FAIL(`AttributeError: ... has no attribute 'write_wiki_first_layout'` / `ImportError: cannot import name 'WIKI_FIRST_DIRS'`)。

- [ ] **Step 3: 实现 —— 加常量 + 方法**

在 `src/services/project_setup.py` 文件顶部,`SERVER_NAME = "shinehe-kb"`(line 25)之后追加两个模块级常量:

```python
SERVER_NAME = "shinehe-kb"

# wiki-first 目录契约:相对项目根的 8 个目录
WIKI_FIRST_DIRS: tuple[str, ...] = (
    "raw",
    "wiki/sources",
    "wiki/entities",
    "wiki/concepts",
    "wiki/comparisons",
    "wiki/syntheses",
    "schema",
    "artifacts/eval",
)

AGENTS_MD_TEMPLATE = """\
# AGENTS.md

> ShineHeKnowledge wiki-first 知识维护规约。由 `shinehe init` 生成,可自由定制。

## Source of truth
- `raw/` 下所有文件只读,agent 不得直接修改
- 所有综合结论必须可追溯到 `raw/` 文件或已有 wiki 页

## Page types
- `wiki/sources/*.md`     单源摘要页(规则模板生成)
- `wiki/entities/*.md`    实体页(LLM 维护)
- `wiki/concepts/*.md`    概念页(LLM 维护)
- `wiki/comparisons/*.md` 对比页(query 回写)
- `wiki/syntheses/*.md`   综合页(query 回写)

## Ingest workflow
- 读取 `raw/` 新源
- 生成 source summary(`wiki/sources/`)
- 识别并更新相关 entities/concepts
- 更新 `wiki/index.md`,追加 `wiki/log.md`
- 与旧结论冲突时显式标注

## Query workflow
- 先读 `wiki/index.md` 定位相关页
- 再读相关 wiki 页
- 证据不足时回到 `raw/` 检索
- 高价值回答可保存为新 wiki 页(`comparisons/syntheses`,draft 状态)

## Lint workflow
- 孤儿页、矛盾、过时 claim、缺失 backlinks 四类检查
- 发现问题标注待修,不自动删除
"""
```

在 `ProjectSetupService` 类内,`write_config` 方法**之前**(约 line 138 `# 配置文件写入` 注释块下)插入新方法:

```python
    def write_wiki_first_layout(self, base_dir: Path) -> list[Path]:
        """在 base_dir 下创建 wiki-first 目录契约 + schema/AGENTS.md。

        创建 raw/、wiki/{sources,entities,concepts,comparisons,syntheses}/、
        schema/、artifacts/eval/ 共 8 个目录,并写入 schema/AGENTS.md 模板。
        幂等:已存在的目录保留;已存在的 AGENTS.md 不覆盖(尊重用户定制)。

        Args:
            base_dir: 项目根目录

        Returns:
            创建(或已存在)的目录路径列表
        """
        base = Path(base_dir)
        created: list[Path] = []
        for rel in WIKI_FIRST_DIRS:
            d = base / rel
            d.mkdir(parents=True, exist_ok=True)
            created.append(d)

        agents_md = base / "schema" / "AGENTS.md"
        if not agents_md.exists():
            agents_md.write_text(AGENTS_MD_TEMPLATE, encoding="utf-8")

        return created
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_project_setup.py::TestWikiFirstLayout -v`
Expected: 全部 5 个 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/services/project_setup.py tests/test_project_setup.py
git commit -m "feat(knowledge-base): 新增 write_wiki_first_layout 生成目录契约与 AGENTS.md"
```

---

## Task 3: _handle_init 集成 wiki-first 目录生成

**Files:**
- Modify: `src/cli.py:19-40`(`_handle_init`)
- Test: `tests/test_cli.py`(新增 e2e 测试)

**Interfaces:**
- Consumes: `ProjectSetupService.write_wiki_first_layout(base_dir)`(Task 2 产出)
- Produces: `shinehe init` 在项目目录(`--path` 或 cwd)生成四目录 + AGENTS.md

> **Impact 提示**:`_handle_init` 是 CLI 入口符号,改动前可 `gitnexus context({name: "_handle_init"})` 确认调用方。已知调用方仅 `handlers` dict(`src/cli.py:284`),影响面小。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_cli.py` 末尾(文件级,非任何 class 内):

```python
def test_init_creates_wiki_first_layout(tmp_path, monkeypatch):
    """init 命令实际创建 wiki-first 目录契约与 AGENTS.md(e2e,不 mock)"""
    from src.cli import main

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # 把 config 也写到项目目录,避免污染全局 ~/.shinehe
    with pytest.raises(SystemExit) as exc:
        main(["init", "--local", "--force", "--path", str(project_dir)])
    assert exc.value.code == 0

    # config.yaml
    assert (project_dir / "config.yaml").exists()
    # wiki-first 目录契约
    assert (project_dir / "raw").is_dir()
    assert (project_dir / "wiki" / "sources").is_dir()
    assert (project_dir / "wiki" / "entities").is_dir()
    assert (project_dir / "wiki" / "concepts").is_dir()
    assert (project_dir / "wiki" / "comparisons").is_dir()
    assert (project_dir / "wiki" / "syntheses").is_dir()
    assert (project_dir / "schema").is_dir()
    assert (project_dir / "artifacts" / "eval").is_dir()
    # AGENTS.md
    agents_md = project_dir / "schema" / "AGENTS.md"
    assert agents_md.exists()
    assert "Source of truth" in agents_md.read_text(encoding="utf-8")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_cli.py::test_init_creates_wiki_first_layout -v`
Expected: FAIL —— `AssertionError`(目录不存在,因为 `_handle_init` 还没调用 `write_wiki_first_layout`)。

- [ ] **Step 3: 实现 —— 改 _handle_init**

改 `src/cli.py:19-40` 的 `_handle_init`,在 `write_config` 之后、`if request["clients"]` 之前插入 wiki-first 目录生成。完整替换为:

```python
def _handle_init(args: argparse.Namespace) -> int:
    """处理 init 子命令"""
    from src.services.project_setup import ProjectSetupService

    service = ProjectSetupService()
    request = {
        "local": args.local,
        "path": args.path,
        "provider": args.provider,
        "clients": [c.strip() for c in args.client.split(",")] if args.client else [],
        "force": args.force,
    }
    config = service.build_config(request)
    target = Path(args.path) if args.path else None
    config_path = service.write_config(target, config, force=args.force)
    print(f"[OK] 配置已写入: {config_path}")

    # wiki-first 目录契约:在项目目录创建 raw/wiki/schema/artifacts + AGENTS.md
    project_dir = Path(args.path) if args.path else Path.cwd()
    layout = service.write_wiki_first_layout(project_dir)
    print(f"[OK] wiki-first 目录已就绪: {project_dir} ({len(layout)} 个目录 + AGENTS.md)")

    if request["clients"]:
        server_config = service.build_server_config(config_path)
        service.configure_clients(request["clients"], server_config)

    return 0
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_cli.py -v`
Expected: 全部 PASS(新 e2e + 原 mock 测试均不受影响,因 mock 测试 patch 了 `_handle_init` 本身)。

- [ ] **Step 5: 提交**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(knowledge-base): init 命令集成 wiki-first 目录契约生成"
```

---

## Task 4: chroma_dir legacy 清理 + W1 回归

**Files:**
- Modify: `config.example.yaml:135`(删 `chroma_dir` 行)
- Modify: `config.example.yaml:124-162`(收敛 5 项安全默认值,与 Task 1 builder 对齐)
- Test: `tests/test_project_setup.py`(新增 config.example 一致性测试)

**Interfaces:**
- 无新接口。本任务让 `config.example.yaml` 与 `build_config` 产出的默认值一致。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_project_setup.py` 末尾(文件级新 class):

```python
# ---------------------------------------------------------------------------
# config.example.yaml 一致性测试
# ---------------------------------------------------------------------------


class TestConfigExampleConvergence:
    """config.example.yaml 与 build_config 默认值一致性"""

    @pytest.fixture(scope="class")
    def example_config(self):
        import yaml
        project_root = Path(__file__).resolve().parent.parent
        with open(project_root / "config.example.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_no_chroma_dir(self, example_config):
        """legacy chroma_dir 已清理"""
        assert "chroma_dir" not in example_config.get("storage", {})

    def test_wiki_safe_defaults(self, example_config):
        """wiki 安全默认:auto_publish=False, lint_contradictions=True"""
        wiki = example_config["wiki"]
        assert wiki["auto_publish"] is False
        assert wiki["lint_contradictions"] is True

    def test_mcp_exposes_wiki_tools(self, example_config):
        """experimental_tools_enabled=True, write_policy=local_confirm"""
        mcp = example_config["mcp"]
        assert mcp["experimental_tools_enabled"] is True
        assert mcp["write_policy"] == "local_confirm"
```

注意:`test_project_setup.py` 顶部需有 `from pathlib import Path` 与 `import pytest`。`pytest` 已 import(line 8);`Path` 需补 —— 在 import 区加 `from pathlib import Path`。

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_project_setup.py::TestConfigExampleConvergence -v`
Expected: 3 个测试 FAIL(`assert True is False` 等,因为 config.example.yaml 还是旧默认值)。

- [ ] **Step 3: 实现 —— 改 config.example.yaml**

改 `config.example.yaml`:

(a) `mcp` 段(line 124-131)收敛默认值:

```yaml
mcp:
  tool_profile: extended    # core | extended | admin | full | legacy
  enable_legacy_aliases: false
  experimental_tools_enabled: true     # 暴露 wiki/graph 工具组(wiki-first 对齐)
  write_policy: local_confirm          # 写操作本地确认("" | preview_only | local_confirm | token_required | disabled)
  allow_http_write: false   # HTTP 模式是否允许写操作
  bind_host: 127.0.0.1     # MCP HTTP 服务绑定地址
  auth_token: ""            # write_policy=token_required 时使用的认证 token
```

(b) `storage` 段(line 134-138)删 `chroma_dir`:

```yaml
storage:
  data_dir: data
  db_name: kb.db
  graph_dir: graph
```

(c) `wiki` 段(line 144-151)收敛默认值:

```yaml
wiki:
  enabled: true
  auto_compile: true
  auto_link: true
  auto_publish: false            # review gate:自动编译、非自动发布、审阅后发布
  lint_contradictions: true      # 启用 wiki lint 闭环
  max_llm_calls_per_ingest: 3
  query_save_min_length: 100
```

- [ ] **Step 4: 运行测试验证通过 + W1 全量回归**

Run(一致性测试):
```bash
python -m pytest tests/test_project_setup.py::TestConfigExampleConvergence -v
```
Expected: 3 个 PASS。

Run(W1 涉及模块回归):
```bash
python -m pytest tests/test_project_setup.py tests/test_cli.py tests/test_doctor.py -v
```
Expected: 全部 PASS,无回归。

Run(确认全仓库无 chroma 残留引用):
```bash
grep -rn "chroma" src/ tests/ scripts/ evals/ || echo "OK: 无 chroma 引用"
```
Expected: `OK: 无 chroma 引用`(或仅文档/注释命中,无代码读取 `storage.chroma_dir`)。

- [ ] **Step 5: 提交**

```bash
git add config.example.yaml tests/test_project_setup.py
git commit -m "chore(knowledge-base): 收敛 config.example 安全默认值并清理 chroma_dir legacy"
```

---

## W1 阶段验收(Phase Review checkpoint)

W1 全部 4 个 Task 完成后,运行以下验收门禁,全部通过方可进入 W2:

```bash
# 1. W1 新增/改动模块全绿
python -m pytest tests/test_project_setup.py tests/test_cli.py tests/test_doctor.py -v

# 2. 全量回归不退化(基线 ~951 passed)
python -m pytest tests/ -v

# 3. 手动 smoke:在临时目录跑一次 init,确认产物
python -c "
import tempfile, os
from pathlib import Path
from src.services.project_setup import ProjectSetupService
d = Path(tempfile.mkdtemp()) / 'proj'
d.mkdir()
s = ProjectSetupService()
cfg = s.build_config({'local': True})
p = s.write_config(d, cfg)
s.write_wiki_first_layout(d)
print('config:', p.exists())
print('raw:', (d/'raw').is_dir())
print('wiki/sources:', (d/'wiki'/'sources').is_dir())
print('AGENTS.md:', (d/'schema'/'AGENTS.md').exists())
"
```

**W1 Definition of Done**(对应 spec §3 成功标准 S1、S7):
- [ ] S1:`shinehe init` 生成 `raw/wiki/schema/artifacts/` + `AGENTS.md`(Task 2+3 验证)
- [ ] S7:README 与 config 无矛盾(`TestConfigExampleConvergence` 验证 config 侧;README 措辞修正留 W4)
- [ ] `build_config` 产出 `knowledge_workflow` 段 + 安全默认值(Task 1)
- [ ] `chroma_dir` legacy 清理(Task 4)
- [ ] 全量测试无回归

**Review 通过后**:进入 W2 编译器阶段(另起 plan:`docs/superpowers/plans/YYYY-MM-DD-...-w2.md`)。

---

## Self-Review 记录

- **Spec coverage**:spec §6.1 任务 1.1 → Task 2+3;1.2 → Task 1(build_config 加段 + `Config.get` 缺省 `legacy` 在后续阶段读取时体现);1.3 → Task 1+4;1.4 → Task 4。全覆盖。
- **Placeholder scan**:无 TBD/TODO;每步含完整可执行代码。
- **Type consistency**:`_wiki_first_defaults` / `write_wiki_first_layout` / `WIKI_FIRST_DIRS` / `AGENTS_MD_TEMPLATE` 命名在 task 间一致;`Config.get("knowledge_workflow.mode", "legacy")` 缺省值与 spec §4.3 一致。
- **向后兼容**:`Config.get` 缺省 `legacy` 保证老配置无 `knowledge_workflow` 段时行为不变。
