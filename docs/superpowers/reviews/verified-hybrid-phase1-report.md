# Phase 1 阶段报告：模式与配置语义

> 日期：2026-07-13  
> Spec：`docs/ShineHeKnowledge 融合收束开发规格说明.md` §16 Phase 1  
> 前置：Phase 0 baseline `abbfa35`

SHA: `737d0a9930c8cb779e308d109a005b2f1af184fa`

---

## 1. 修改文件列表

| 文件 | 变更 |
|---|---|
| `src/utils/knowledge_mode.py` | **新增** 模式解析 / 兼容映射 / 能力位 |
| `src/services/project_setup.py` | verified/authoring/evidence_only 默认配置 |
| `src/cli.py` | `init --mode`；仅 authoring 建布局；状态输出 |
| `src/services/doctor.py` | `check_knowledge_mode` 基础检查 |
| `src/services/knowledge_workflow.py` | authoring/wiki_first 门控 |
| `src/services/rag_pipeline.py` | size_aware / wiki parent 门控改 authoring |
| `config.example.yaml` | 新默认 verified + serving 配置骨架 |
| `docs/migration/v1.6-to-v1.7-verified-hybrid.md` | 迁移文档骨架 |
| `tests/test_knowledge_mode.py` | **新增** 解析单元测试 |
| `tests/test_project_setup.py` | 默认 verified / authoring 用例 |
| `tests/test_cli.py` | mode 解析与布局条件 |
| `tests/test_doctor.py` | knowledge mode 检查 |
| `tests/test_size_aware_legacy.py` | authoring 注入断言 |

## 2. 行为变化摘要

- **新默认**：`shinehe init` → `knowledge_workflow.mode=verified`
- **默认不写**：`mcp.write_policy=disabled`，`experimental_tools_enabled=false`，`tool_profile=core`
- **读写分离**：`wiki.read_enabled` / `wiki.authoring_enabled`
- **兼容**：`wiki_first`→authoring（compile 仍跑）；`legacy`→evidence_only
- **布局**：仅 `--mode authoring` 创建 wiki Authoring 目录
- **不改用户文件**：运行时映射，Doctor 提示弃用别名

## 3. 兼容性

| 场景 | 结果 |
|---|---|
| 已有 `mode: wiki_first` | 等价 authoring，维护能力保留 |
| 已有 `mode: legacy` | 等价 evidence_only |
| 新 init | verified，无 Authoring 目录 |
| Serving / Hybrid 检索 | **未实现**（Phase 2/3） |

## 4. 测试

见提交信息与本轮 pytest 输出。

## 5. 指标

Phase 1 不改检索路径，Raw/Wiki 评测指标应与 Phase 0 持平（不重跑全量 eval，除非回归失败）。

## 6. 风险

- 无 `mode` 的旧环境默认从代码路径上的 `legacy` 语义变为 `verified`（仍不跑 authoring compile）
- `search_mode: hybrid_verified` 写入配置但 Phase 3 前管线仍走既有 blend 路径
- Doctor Serving Claim 统计为占位

## 7. 回滚

```bash
git revert 737d0a9
```

无 DB schema 变更。用户可将 `mode` 设回 `wiki_first` / `legacy`。

## 8. 明确未做（禁止超范围）

- WikiServingGate
- Raw+Wiki 融合检索
- Claim 引用/冲突处理
- 维护中心自动化
- MCP 写工具面进一步收缩（除 init 默认外）
