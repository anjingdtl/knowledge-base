# MCP 集成指南

## 什么是 MCP？

Model Context Protocol（MCP）是一个开放协议，让 AI 应用能够安全地访问外部工具和数据源。ShineHeKnowledge 当前注册 51 个原始工具和 51 个命名空间别名，让 AI 编码工具可以检索、问答和管理本地知识。

## 支持的 AI 工具

ShineHeKnowledge 提供了主流 AI 编码工具的一键配置模板：

| 工具 | 配置方式 |
|------|----------|
| **Claude Desktop / Claude Code** | 复制 JSON 到 MCP 配置 |
| **Cursor** | 在 Settings → MCP 中添加 |
| **Cline** | 通过 VS Code 扩展配置 |
| **Windsurf** | 在 Cascade 设置中添加 |

## 常用 MCP 工具

### 知识管理
- `create` — 创建知识条目
- `search` — 语义搜索知识库
- `read` — 读取知识条目详情
- `update` — 更新知识条目
- `delete` — 删除知识条目

### RAG 问答
- `ask` — 向知识库提问
- `ask_with_query` — 使用自定义查询策略提问

### Wiki 操作
- `save_to_wiki` — 将问答保存为 Wiki 页面
- `wiki_approve` — 审核通过 Wiki 页面
- `fix_dead_references` — 修复 Wiki 死链

### 文件导入
- `ingest_file` — 导入本地文件到知识库
- `ingest_url` — 导入网页内容

## 配置示例

ShineHeKnowledge 运行 MCP Server 后，在你的 AI 工具配置中添加：

```json
{
  "mcpServers": {
    "shinehe-kb": {
      "command": "python",
      "args": ["run_mcp.py"],
      "cwd": "/path/to/knowledge-base"
    }
  }
}
```

或使用 HTTP 模式：

```json
{
  "mcpServers": {
    "shinehe-kb": {
      "url": "http://localhost:9000/mcp"
    }
  }
}
```
