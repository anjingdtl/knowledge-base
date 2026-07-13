# 客户端接入

## 快速开始

```bash
pip install -e ".[all]"
shinehe init --local --path D:\docs --client claude-code
shinehe index D:\docs
shinehe mcp --transport stdio
```

## 模板

| 客户端 | 模板 |
|---|---|
| Claude Desktop | `mcp_config_templates/claude_desktop.json` |
| Cursor | `mcp_config_templates/cursor.json` |
| Cline | `mcp_config_templates/cline.json` |
| Continue | `mcp_config_templates/continue.json` |

## 推荐 Agent 流程

1. `kb_capabilities` — 查看 mode / write_policy / 可见工具  
2. `search` / `ask` — 检索与问答  
3. `read` — 按 `knowledge_id` / `block_id` / `claim_id` 溯源  

Verified 默认无写工具；需要索引时用 CLI `shinehe index` 或调高 write_policy。
