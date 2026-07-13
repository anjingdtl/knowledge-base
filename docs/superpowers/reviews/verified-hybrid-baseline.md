# Verified Hybrid Baseline（Phase 0）

> 文档状态：Baseline frozen  
> 日期：2026-07-13  
> 基线提交：`45113ff`（`feat: add vector coverage maintenance repair`）  
> 源码版本：`1.6.0`  
> Spec：`docs/ShineHeKnowledge 融合收束开发规格说明.md`  
> 约束：**本阶段不修改生产行为**，仅冻结测试与配置基线，供 Phase 1+ 对照。

---

## 1. 范围

按融合收束 Spec §16 Phase 0 冻结：

- 全量 pytest / Ruff / mypy / 前端 build / Docker 可用性
- Raw Retrieval Eval / Wiki Eval / Knowledge Evolution Eval
- 当前 MCP 工具面与 init 默认配置
- 本地 DB 中 Wiki / Claim 可 Serving 相关计数
- 查询延迟样本

---

## 2. 代码与配置基线

| 项 | 值 |
|---|---|
| Git HEAD | `45113ff66cabb53a8af66f185e1ee475021d4af2` |
| Branch | `master` |
| VERSION | `1.6.0` |
| 工作树本地 `config.yaml` mode | `wiki_first` |
| 本地 MCP | `tool_profile=extended`, `write_policy=local_confirm`, `experimental_tools_enabled=true` |
| 本地 Wiki | `enabled=true`, `auto_compile=true`, `auto_publish=false` |
| `canonical_v2` | 示例配置默认 `off`（见 `config.example.yaml`） |

### 2.1 当前 init 默认（`ProjectSetupService.build_config`）

| 路径 | knowledge | MCP write_policy | experimental tools | auto_publish |
|---|---|---|---|---|
| `--local` | `wiki_first` | `disabled` | `true` | `false` |
| `--provider siliconflow` | `wiki_first` | `local_confirm` | `true` | `false` |

完整快照：`artifacts/eval/phase0-init-and-tools.json`。

### 2.2 MCP 工具面（代码定义）

| Profile | 工具数 | 说明 |
|---|---:|---|
| core | 10 | ping / kb_capabilities / search / ask / read / list_knowledge / index_path / get_job / list_jobs / reindex_all |
| extended | 20 | core + Query DSL / source graph / async ingest |
| admin | 30 | extended + CRUD / audit / undo |
| full | 非 experimental 全量 | 仍需 `experimental_tools_enabled` 才暴露 wiki/graph/memory |
| legacy | 全量 + 别名 | 兼容 `kb.*` 命名空间 |

契约快照：

- `tests/snapshots/mcp_tools.json` — 30 个工具（契约面）
- `tests/snapshots/mcp_tools_legacy.json` — 103 个工具（含别名历史面）

---

## 3. 门禁结果

### 3.1 全量 pytest

```text
命令: PYTHONPATH=. python -m pytest tests/ -q --tb=line
结果: 1556 passed, 2 skipped, 16 warnings in 385.09s
退出码: 0
产物: artifacts/eval/phase0-pytest.txt
```

说明：

- 相对 PROGRESS 中 Phase 6 记录的 `1516 passed / 5 xfailed`，本基线 **0 xfailed**（C2 matcher 收紧已并入 master）。
- 警告主要为 `mcp_post_fix_test` 的 return-not-None，以及 Windows 下 GUI 测试子进程编码噪声；**无失败**。

### 3.2 Ruff

```text
命令: ruff check src tests
结果: 6 errors（均可 --fix），均在 tests/test_v160_stability_report_fixes.py
退出码: 1
产物: artifacts/eval/phase0-ruff.txt
```

| 规则 | 位置 | 性质 |
|---|---|---|
| I001 | import 排序 | 测试文件样式 |
| F401 | 未使用 import | 测试文件样式 |

**判定：生产代码 `src/` 无 ruff 报错；测试文件既有风格债，Phase 0 不修（禁止混入无关改动）。**

### 3.3 mypy

```text
命令: python -m mypy src
结果: Found 2 errors in 1 file (checked 191 source files)
退出码: 1
产物: artifacts/eval/phase0-mypy.txt
```

| 文件 | 错误 |
|---|---|
| `src/services/file_graph.py:323,330` | `Returning Any from function declared to return "str \| None"` |

**判定：既有类型债；Phase 0 不修。**

### 3.4 前端

```text
命令: cd client && npm run build
结果: tsc + vite build OK（~608ms）
退出码: 0
产物: artifacts/eval/phase0-frontend-build.txt
```

### 3.5 Docker

```text
结果: 本机 docker 不可用（docker not available）
产物: artifacts/eval/phase0-docker.txt
```

**风险登记：Docker 镜像构建未在本机复验；后续发布阶段需在有 Docker 的环境补跑。**

---

## 4. 评测结果

### 4.1 Raw Retrieval Eval

```text
命令: python evals/run_retrieval_eval.py --all --fake-embedding --report text
结果: Overall PASS
产物: artifacts/eval/phase0-retrieval-eval.txt
```

| Dataset | Recall@5 | MRR | nDCG@10 | Latency P50 |
|---|---:|---:|---:|---:|
| retrieval_code | 1.0000 | 1.0000 | 1.0000 | 0.3ms |
| retrieval_no_answer | 0.0000* | 0.0000 | 0.0000 | 0.3ms |
| retrieval_table | 1.0000 | 1.0000 | 0.9779 | 0.3ms |
| retrieval_zh | 0.6000 | 0.3400 | 0.4036 | 0.3ms |

\* no_answer 集以拒答/空结果为成功语义；`No-Answer=0.6667` 计入 PASS 门禁。

> 注意：本结果使用 **fake-embedding**，不得宣传为真实 embedding 模型效果（Spec 禁止事项）。

### 4.2 Wiki Eval

```text
命令: python evals/run_wiki_eval.py
结果: 退出码 0
产物: artifacts/eval/phase0-wiki-eval.txt
```

| 指标 | 值 |
|---|---:|
| source_coverage | 1.0 |
| cross_page_update_rate | 0.9167 |
| orphan_page_rate | 0.8333 |
| query_save_rate | 0.0909 |
| stale_claim_ratio | 0.0 |

> 注：相对历史 Phase 5/6 记录的 `cross_page_update 0.9545`，本机 fixture 跑出 0.9167。属评测环境/数据差异，**以本文件数值为融合收束基线**；后续阶段不得为刷分删除困难样本。

### 4.3 Knowledge Evolution Eval

```text
命令: python evals/run_knowledge_evolution_eval.py
结果: Overall PASS
产物: artifacts/eval/phase0-evolution-eval.txt
```

全部门槛指标 PASS（claim provenance / evidence location / merge / update propagation / unsupported detection / page identity / migration parity）；`projection_parity` SKIP-as-pass（未注入 projection）。

---

## 5. 本地 DB 知识与 Claim 快照

来源：`data/kb.db`（开发机本地数据，非 CI fixture）。

产物：`artifacts/eval/phase0-db-stats.json`

| 对象 | 数量 | 备注 |
|---|---:|---|
| knowledge_items | 44 | Raw 证据层 |
| blocks | 191 | Block 证据 |
| indexed_files | 11 | 路径索引 |
| wiki_pages（legacy 读模型） | 20 | published=17, draft=1, deleted=2 |
| wiki_pages_v2（Canonical） | 0 | 本库尚未填入 Canonical page |
| wiki_claims | 0 | **可 Serving Claim = 0** |
| wiki_claim_evidence | 0 | 无 Evidence 绑定 |

**产品含义（冻结事实）：**

1. 当前开发库以 Raw Retrieval + legacy `wiki_pages` 为主。
2. Canonical Claim 层为空 → 按 Spec，Verified 档应自动降级为 Raw，不得报错。
3. Phase 2 Serving Gate 验收前，任何 “Serving Claim 数” 均应以 Gate 过滤后的 active+evidence 为准；基线为 **0**。

---

## 6. 延迟样本

| 来源 | P50 / 样本 | 说明 |
|---|---|---|
| Retrieval eval（fake-embedding） | ~0.3ms | 离线确定性检索管线 |
| FTS 本地探测 | ~0ms 量级 | `Database` 方法探测；未代表 hybrid+embedding 端到端 |

产物：`artifacts/eval/phase0-latency.json`  
端到端 hybrid（真实 embedding + rerank）延迟 **未在本阶段测量**（避免依赖外部 API 配额）；Phase 3 融合后应补 hybrid latency 对照。

---

## 7. 模式语义现状（改造前）

| 配置值 | 当前代码行为 | Spec 目标 |
|---|---|---|
| `wiki_first` | 默认 init；触发 compile / size_aware / wiki parent | 映射为 `authoring` |
| `legacy` | Config 缺省回退；不跑 wiki compile | 映射为 `evidence_only` |
| （无） | 多处 `Config.get(..., "legacy")` | 新默认 `verified` |
| `verified` / `authoring` / `evidence_only` | **尚不存在** | Phase 1 引入 |

缺口：

- Wiki Read 与 Authoring 未拆分配置位；
- 无统一 Serving Gate；
- Raw 与 Wiki 检索未在 SearchService 融合；
- 维护中心尚未成为统一控制面。

---

## 8. 风险与注意事项

1. **Ruff/mypy 既有失败** 不是本阶段回归，但后续 CI 若强制 0 error 需单独清理。
2. **Docker 未复验**。
3. **Wiki eval orphan_page_rate=0.8333** 偏高，说明 fixture/wiki 质量债仍在；不得为降 orphan 而改评测定义。
4. **本地 Canonical Claim=0**，Hybrid 融合收益需在有 Claim 的数据集上重测。
5. 工作树存在未跟踪的 Spec 副本与本地 raw/ 数据，**不纳入基线提交**。

---

## 9. 回滚方式

Phase 0 仅新增基线文档与 `artifacts/eval/phase0-*` 产物：

```bash
git revert <phase0-commit-sha>
# 或删除
# docs/superpowers/reviews/verified-hybrid-baseline.md
# artifacts/eval/phase0-*
```

不涉及 schema、配置默认值或运行路径变更。

---

## 10. 下一阶段入口

Phase 1（**仅**）：

- 引入 `verified / authoring / evidence_only`
- 兼容 `wiki_first` → authoring、`legacy` → evidence_only
- ProjectSetup / CLI / Config Example / Doctor 基础状态
- 默认 verified；已有 `wiki_first` 用户不被切成只读
- **不**实现 Serving Gate / Hybrid 融合 / 维护中心（Phase 2+）
