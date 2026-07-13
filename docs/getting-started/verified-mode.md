# Verified 模式（默认）

面向日常 AI Agent 检索与问答。

## 行为

| 能力 | 状态 |
|---|---|
| 原始文档检索 (Raw) | 开启 |
| 已验证 Wiki Claim 读取 | 开启（Serving Gate） |
| Wiki Authoring / 编译写 | **关闭** |
| MCP 写工具 | 默认 `write_policy=disabled` 时不注册 |
| 保护性维护 (R1) | 可自动（不改变语义） |
| 自动发布 | **关闭** |

## 初始化

```bash
shinehe init --local --path D:\docs --client claude-code
# 等价于 --mode verified
```

## 配置要点

```yaml
knowledge_workflow:
  mode: verified
wiki:
  read_enabled: true
  authoring_enabled: false
  auto_publish: false
rag:
  verified_knowledge:
    enabled: true
mcp:
  tool_profile: core   # 或 extended
  write_policy: disabled
```

## 降级

- 无可用 Claim → 自动 Raw
- Wiki / Gate 异常 → 自动 Raw，不阻断回答
