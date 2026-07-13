# Authoring 模式

显式开启的 Wiki 维护档：构建 Canonical 知识、审阅与发布。

## 行为

| 能力 | 状态 |
|---|---|
| Raw 检索 | 开启 |
| Verified Wiki 读 | 开启 |
| Wiki 目录布局 | 初始化时创建 |
| 语义 Draft (R3) | 可生成，**须人工审阅** |
| 发布 / 删除 / 迁移 (R4) | **必须人工确认** |
| MCP 写 | 需非 `disabled` 的 write_policy |

## 初始化

```bash
shinehe init --mode authoring --local --path D:\knowledge
```

兼容旧配置：`knowledge_workflow.mode: wiki_first` 运行时映射为 `authoring`，**不会静默改写配置文件**。

## 安全边界

- `auto_publish` 默认 false
- Authoring MCP 工具仍受 `write_policy` 与 experimental 开关约束
- 内部保护性维护 ≠ Agent 写权限
