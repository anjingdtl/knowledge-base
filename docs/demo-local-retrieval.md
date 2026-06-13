# 本地检索功能演示

本文档描述如何使用 `scripts/demo_local_retrieval.py` 演示 ShineHeKnowledge 的本地检索功能。

## 演示流程

演示脚本自动完成以下步骤：

1. **创建测试文档** — 在临时目录生成 3 个 Markdown 文档（Python 教程、数据库指南、API 设计）
2. **初始化配置** — 生成最小化 `config.yaml`，使用 Ollama 本地模型
3. **索引文档** — 调用 `indexer.index_directory()` 索引所有文档
4. **搜索查询** — 执行 "Python 函数定义" 查询，验证检索结果
5. **修改文档** — 向 Python 教程添加"异常处理"章节
6. **增量更新** — 再次调用索引，验证增量更新机制
7. **再次搜索** — 执行 "异常处理 try-except" 查询，验证新内容可检索

## 使用方法

### 基本用法

```bash
python scripts/demo_local_retrieval.py
```

演示完成后自动清理临时目录。

### 保留工作目录

```bash
python scripts/demo_local_retrieval.py --keep
```

用于调试，保留工作目录供检查。

### 指定工作目录

```bash
python scripts/demo_local_retrieval.py --workdir /path/to/workdir
```

使用指定目录作为工作目录。

### 输出 JSON 结果

```bash
python scripts/demo_local_retrieval.py --json-output results.json
```

将演示结果保存为 JSON 文件。

## 输出示例

```
============================================================
本地检索功能演示
============================================================

[1/7] 创建测试文档...
✓ 创建了 3 个测试文档

[2/7] 初始化配置...
✓ 配置已写入: /tmp/shinehe_demo_xxx/config.yaml

[3/7] 索引文档...
✓ 索引完成: 3 个文档, 45 个块

[4/7] 搜索查询: 'Python 函数定义'
✓ 找到 3 个结果
  最佳匹配: python_tutorial.md
  相关度: 0.823
  ✓ 引用完整: 包含内容和来源

[5/7] 修改文档 (添加新内容)...
✓ 文档已更新

[6/7] 增量更新索引...
✓ 增量更新完成: 1 个文档已更新

[7/7] 搜索新内容: '异常处理 try-except'
✓ 找到 2 个结果
  最佳匹配: python_tutorial.md
  相关度: 0.791
  ✓ 成功检索到新增内容

============================================================
演示结果:
  初始搜索命中: ✓
  增量更新成功: ✓
  引用完整性: ✓
============================================================
```

## 验证指标

演示脚本验证三个核心指标：

| 指标 | 说明 | 预期 |
|------|------|------|
| `initial_hit` | 初始搜索能否命中结果 | `true` |
| `incremental_update` | 增量更新能否检测到文档变更 | `true` |
| `citation_complete` | 搜索结果是否包含完整引用（内容 + 来源路径） | `true` |

所有指标均为 `true` 时，脚本退出码为 0；否则为 1。

## 前置条件

- Python 3.10+
- 已安装项目依赖：`pip install -e ".[parsers]"`
- Ollama 服务运行中（或使用其他 embedding/LLM 端点）

## 自定义配置

如需使用不同的 embedding/LLM 端点，可修改演示脚本中的 `config_content` 模板，或手动创建 `config.yaml` 后传入 `--workdir` 参数。

## 故障排查

### 索引失败

检查 `config.yaml` 中的 `embedding.base_url` 是否可达。

### 搜索无结果

1. 确认 embedding 模型已下载（Ollama: `ollama pull nomic-embed-text`）
2. 检查 `data/` 目录是否生成了 SQLite 数据库
3. 使用 `shinehe doctor` 诊断配置

### 增量更新未检测到变更

确认文件修改时间确实发生变化（某些文件系统时间精度较低）。

## 相关文档

- [README Quick Start](../README.md#quick-start)
- [Agent Usage](mcp/agent-usage.md)
- [Retrieval Quality](retrieval-quality.md)
