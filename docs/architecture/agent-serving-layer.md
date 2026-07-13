# Agent Serving Layer

统一 MCP / API / GUI 读路径：

- `search` / `ask` / `read`  
- Claim + Evidence 双层引用  
- `kb_capabilities` 暴露 mode、serving 状态、hidden_by_policy  

默认 **verified**：读已验证知识，不向 Agent 暴露维护写工具。
