# Authoring 安全

1. 仅 `knowledge_workflow.mode=authoring` 且 `wiki.authoring_enabled=true`  
2. `mcp.write_policy != disabled` 才注册写工具  
3. `auto_publish` 默认 false  
4. R3 只生成 Draft；R4 必须人工确认  
5. 所有写入走 Operation Log  

详见 `docs/getting-started/authoring-mode.md`。
