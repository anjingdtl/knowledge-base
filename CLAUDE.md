# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目速查: ShineHeKnowledge

本地知识库系统（v1.0.0+），支持多模态文档管理、RAG 智能问答，三种运行模式共享同一服务层。

### 运行命令

```bash
# 安装
pip install -e .                # MCP 核心
pip install -e ".[all]"        # GUI + API + 解析器

# 运行
python main.py                  # 桌面 GUI
python run_api.py               # REST API (端口 8000)
python run_mcp.py               # MCP Server (stdio 模式)
shinehe-mcp -t streamable-http --port 9000  # MCP HTTP 模式

# 测试
pytest tests/ -v               # 全部测试
pytest tests/test_db.py -v     # 单文件测试
```

### 架构概述

```
knowledge-base/
├── main.py / run_api.py / run_mcp.py  # 三种入口
└── src/
    ├── api/routes.py           # FastAPI 路由 (Bearer Token 认证)
    ├── gui/                    # PySide6 桌面界面
    │   ├── main_window.py      # 主窗口
    │   ├── knowledge_view.py   # 知识条目视图
    │   ├── chat_view.py        # RAG 问答界面
    │   └── wiki_view.py        # Wiki 页面管理
    ├── services/               # 核心服务层（所有模式共享）
    │   ├── db.py               # SQLite + FTS5 存储
    │   ├── vectorstore.py      # ChromaDB 向量存储
    │   ├── hybrid_search.py    # 向量 + 关键词混合搜索 (RRF 融合)
    │   ├── rag_pipeline.py     # RAG 可配置管线
    │   ├── query_rewriter.py   # 查询改写
    │   ├── reranker.py         # LLM 重排序
    │   ├── wiki_*.py           # Wiki 系统 (compiler/workflow/site/renderer/seo/lint)
    │   ├── graph_builder.py    # 知识图谱构建 (LLM 分析关系)
    │   ├── file_parser.py      # 多模态文件解析 (PDF/DOCX/图片/HTML/Excel)
    │   ├── async_*.py           # 异步任务系统 (task/tasks/worker)
    │   └── librarian.py        # 文本切分与批量索引
    ├── models/                 # 数据模型
    ├── utils/config.py         # Config 单例（驱动所有模式）
    └── version.py              # 版本号唯一来源
```

### RAG 管线

可配置阶段（config.yaml → rag.pipeline.stages）：

1. **query_rewrite** — 查询改写（LLM 生成多版本）
2. **wiki_retrieval** — Wiki 知识检索（FTS5）
3. **vector_search** — 向量搜索（HybridSearcher: 向量 + 关键词 + RRF 融合）
4. **rerank** — LLM 打分重排序
5. **generate** — LLM 生成回答
6. **postprocess** — 后处理（去重、截断）

支持自定义阶段（custom_stages 配置）。

### Wiki 工作流

状态机 (`wiki_workflow.py`): draft → review → published → deprecated

支持状态转换验证、版本快照、工作流历史记录。可通过配置 `wiki.auto_publish` 跳过审核直接发布。

### 知识图谱

`graph_builder.py` 通过 LLM 分析知识条目间的语义关系，支持 6 种关系类型：related、contains、references、prerequisite、contradicts、part_of。

### 异步任务系统

`async_task.py` / `async_tasks.py` / `async_worker.py` 提供后台任务处理能力，用于文件解析、索引等耗时操作。

### 配置

`config.yaml` 驱动所有模式，Config 单例在 `src/utils/config.py`。支持任意 OpenAI 兼容供应商（DeepSeek、智谱 GLM、Moonshot、硅基流动、Ollama 本地）。

### 版本发布

1. 修改 `src/version.py` 中的 VERSION
2. `python scripts/build_docs.py` — 生成用户说明文档
3. `python scripts/build_windows.py` — 打 Windows 安装包
4. `python scripts/build_docker.py` — 打 Docker 镜像
