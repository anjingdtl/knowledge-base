# Phase 6 阶段报告：MCP 与 Authoring 安全边界

> 日期：2026-07-13  
> Spec：§9 / §12.8–§12.10 / Phase 6

---

## 1. 修改文件列表

| 文件 | 变更 |
|---|---|
| `src/mcp/tool_registry.py` | `select_tools` 支持 write_policy / knowledge_mode / authoring |
| `src/mcp/tool_profiles.py` | Core 文案：非「纯只读」 |
| `src/mcp_server.py` | 工具选择接线、启动日志、`kb_capabilities` §9.4 字段、`ask` verified 路径、`read` claim/block/page |
| `tests/test_mcp_write_policy_filter.py` | **新增** |

## 2. 行为变化

- `write_policy=disabled`：隐藏 write/destructive（含 full/legacy）
- Verified + authoring_enabled=false：隐藏 wiki 写工具
- Authoring 需 mode=authoring 且 write_policy≠disabled
- `kb_capabilities` 返回 knowledge_mode / verified_wiki_read / wiki_serving_status / hidden_by_policy 等
- `ask` 在 verified hybrid 下走 VerifiedAnswerService
- `read` 支持 claim_id / block_id / page_id 及前缀

## 3. 验收

| 项 | 状态 |
|---|---|
| Verified 默认可无写工具 | ✅（write_policy=disabled） |
| Authoring 仍受 policy | ✅ |
| Legacy 不绕过 write_policy | ✅ |

## 4. 回归

全量 `pytest tests/ -q`：**1639 passed / 2 skipped**

## 5. 回滚

`git revert <phase6-sha>`；`select_tools` 省略新 kwargs 时行为与历史一致。
