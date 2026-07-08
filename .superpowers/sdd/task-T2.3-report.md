# Task T2.3 Report: PageLocator switch to stable canonical page_id

## 1. 文件清单

| 操作 | 文件 | 变更摘要 |
|---|---|---|
| Modify | `src/services/wiki_projection.py` | 添加 `find_page_id_by_path(path)` 公共查询方法 |
| Modify | `src/services/wiki_page_locator.py` | `__init__` 接受 `projection` 参数; `_score_page` 从静态方法改为实例方法, 读取 frontmatter `page_id` 生成稳定 id; 新增 `_enrich_with_projection` 方法补全 legacy 页 page_id; FS fallback + 一次性 warning |
| Modify | `src/core/container.py` | `WikiPageLocator()` → `WikiPageLocator(projection=self.wiki_projection)` |
| Modify | `tests/test_wiki_page_locator.py` | `_make_page` 新增 `page_id=None` 可选参数; 新增 5 个测试 (现有 5 个未改动) |

## 2. 测试结果

### locator 单元测试

```
tests/test_wiki_page_locator.py: 10 passed (5 existing + 5 new), 0 failed
```

### 全量测试

```
tests/: 1295 passed, 1 skipped, 0 failed (baseline 1290 + 5 new)
```

### 集成测试 4 文件全绿

```
tests/test_blend_fusion.py:              5 passed
tests/test_wiki_parent_enrich_stage.py:  9 passed
tests/test_wiki_read_stage.py:          4 passed
tests/test_size_aware_legacy.py:         5 passed
Total:                                   23 passed, 0 failed
```

### 静态检查

```
ruff check src tests:  0 errors
mypy src:              0 errors (179 source files)
```

## 3. 与 brief 偏差

### metadata.path 与 wiki_pages_v2.path 对齐方式

采用 **locator 侧归一化**: `_enrich_with_projection` 中将候选 metadata 的绝对路径通过 `Path.relative_to(self._wiki_dir)` 转为相对路径, 再传给 `find_page_id_by_path`。`find_page_id_by_path` 接收的就是相对路径, 与 `wiki_pages_v2.path` 格式一致。

### 测试 4 选 (b) — 手动 MagicMock

使用 `unittest.mock.MagicMock` mock projection 对象, 设置 `find_page_id_by_path.return_value`, 精确测试 `_enrich_with_projection` 逻辑, 不耦合 T2.2 投影流程。

### locate 签名与 _score_page 重构

- `locate -> (list, int)` 签名完全不变
- `_score_page` 从 `@staticmethod` 改为实例方法 (需要 `self._wiki_dir` 做路径归一化, 但最终归一化在 `_enrich_with_projection` 中完成, `_score_page` 本身不需要实例状态; 改为实例方法是为了未来可扩展性且与 brief 一致)

### 全量测试计数

Brief 估算 ≥1300 (基线 1290 + 10), 实际 1295 (基线 1290 + 5 新测试)。差 5 个是因为只有 5 个测试是新增的, 现有 5 个已计入基线。

## 4. 剩余风险

1. **legacy 页 id 不稳定**: 无 frontmatter `page_id` 且 projection 无数据时, 候选 id 仍是 `wiki:<type>:<slug>`, slug 随文件名变化。只有 canonical_v2 启用且 projection 完成后才会补全。
2. **projection 补全性能**: `_enrich_with_projection` 对每个无 page_id 的候选逐个调用 `find_page_id_by_path` (各一次 SQLite 查询)。候选数通常 ≤ top_n (10), 单次查询微秒级, 不构成瓶颈。
3. **canonical_v2 默认关闭**: 投影表为空 → 所有页面走 FS slug id, 行为与改动前完全一致。只有显式开启 canonical_v2 + 完成 projection 后, 稳定 id 才生效。

## 5. commit SHA

`25b17e6` — `refactor(wiki-v2): resolve wiki candidates by stable page_id`

## Fix Round (I1 + M2)

### I1 — test_find_page_id_by_path_queries_real_table

**File:** `tests/test_wiki_projection.py:314` (测试 12)

直接 INSERT 一行到 `wiki_pages_v2`，通过真实 `WikiProjection` 实例调用 `find_page_id_by_path`，验证真实 SQL `SELECT page_id FROM wiki_pages_v2 WHERE path = ?` 被执行:
- 已有路径 `concepts/foo.md` → 返回 `page_real_1`
- 缺失路径 `concepts/missing.md` → 返回 `None`
- `None` 输入 → 不抛异常，返回 `None`（方法内部 except 兜底）

无 mock，完整覆盖 real SQL 路径。

### M2 — `if pid:` → `if pid is not None:`

**File:** `src/services/wiki_page_locator.py:171`

防御性修正：空字符串 page_id 会被 truthiness 跳过，`is not None` 是精确守卫。

### 测试结果

```
python -m pytest tests/test_wiki_projection.py tests/test_wiki_page_locator.py -v
22 passed, 1 warning

python -m pytest tests/ -q
1296 passed, 1 skipped, 0 failed

ruff check src tests:   0 errors
mypy src:               0 errors (179 source files)
```

### Commit

`5059674` — `test(wiki-v2): cover find_page_id_by_path real sql path`

## Final-Review Fix (I-1 + M-1 + real-projection test)

### I-1 — Windows 路径分隔符导致 legacy 页面 enrichment 静默失败

**文件:** `src/services/wiki_page_locator.py:~170`

`_enrich_with_projection` 用 `str(Path(...).relative_to(...))` 构造 rel_path, 在 Windows 上
产生 backslash 路径 `sources\fttr.md`, 但 `wiki_pages_v2.path` 存储 forward-slash 路径
`sources/fttr.md` (WikiRepository._rel 在 wiki_repository.py:92 做 `.replace("\\", "/")`)。
SQL WHERE 匹配失败 → enrichment 静默 no-op。

**修复:** `str(...)` → `.as_posix()`, 始终输出 forward-slash 路径。

### M-1 — canonical_v2 关闭时 locator enrichment 仍命中 DB

**文件:** `src/services/wiki_page_locator.py:~155-163`, `src/services/wiki_projection.py:~37-39`

container.py:401 始终注入 `projection=self.wiki_projection` (enabled=False 时也注入);
locator 的 `_enrich_with_projection` 没有启用检查, 对每个无 page_id 的候选执行
`find_page_id_by_path` SELECT (表空时仍是 N 次无用索引查询)。

**修复:**
1. `WikiProjection` 添加 `enabled` 公共属性 (返回 `self._enabled`)。
2. `_enrich_with_projection` 入口加门控: `projection is None` → return;
   `getattr(projection, "enabled", True)` 为 falsy → return (非 WikiProjection 对象不受影响)。
3. 新增 `test_locate_disabled_projection_skips_db_queries` 验证 enabled=False 时
   `find_page_id_by_path` 不被调用。

### 真实投影测试 — 覆盖 Windows 路径分隔符回归

**文件:** `tests/test_wiki_page_locator.py::test_locate_real_projection_enriches_with_forward_slash_path`

用真实 WikiRepository + WikiProjection + Database 单例 + 真实 FS 文件:
1. write_markdown 写 legacy 页 `sources/fttr.md` (无 page_id)
2. INSERT wiki_pages_v2 行, path 列用 forward-slash `sources/fttr.md`
3. locator.locate("FTTR") → 验证 enrichment 将 slug id 改为 projection page_id
4. 在 Windows 上, 修复前 `.as_posix()` 缺失会导致 backslash `sources\fttr.md` ≠
   forward-slash `sources/fttr.md` → 测试失败; 修复后测试通过。

### 测试结果

```
python -m pytest tests/test_wiki_page_locator.py tests/test_wiki_projection.py -v
24 passed, 1 warning

python -m pytest tests/ -q
1298 passed, 1 skipped, 0 failed

ruff check src tests:   0 errors
mypy src:               0 errors (179 source files)
```

### Commit

`8edaee8` — `fix(wiki-v2): normalize locator path and gate projection enrichment`
