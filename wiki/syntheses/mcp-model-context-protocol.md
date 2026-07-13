---
schema_version: 1
page_id: 4de4a7d0-463f-4132-b56e-22fa95722dd1
title: MCP（Model Context Protocol）
page_type: syntheses
status: published
revision: 1
aliases: []
tags:
- MCP
- Protocol
- LLM
- AI Tooling
source_ids:
- 351576d7-4c0f-4de1-94c1-58e7106a4545
claim_ids: []
created_at: '2026-07-10T17:56:22.208128'
updated_at: '2026-07-10T17:56:22.208509'
content_hash: sha256:d936ddc1f63a390bd57ec0d41647263c55c2c27cc47f0a8c75bbf68da325e3c5
supersedes_page_id: null
---

# MCP（Model Context Protocol）

## 定义

**MCP**（Model Context Protocol）是一套用于标准化 AI 助手与外部数据源、工具之间通信的协议。它定义了上下文信息的请求、传输与注入格式，使得大语言模型（LLM）能够动态地访问外部能力。

## 与 worker-import 的关系

在 [[worker-import]] 场景中，Cloudflare Worker 作为边缘执行环境，承担了 MCP 资源的导入（import）、解析与分发职责，使 MCP 数据能够在就近节点被高效消费。

## 关键特性

- **标准化**：统一的协议层抽象，便于不同 LLM 客户端复用
- **可扩展**：支持自定义工具、数据源注册
- **边缘友好**：天然适配无服务器运行时（如 Worker）

## 备注

该页面为基于 worker-import 文档提取的简要说明，详细信息以 MCP 官方规范为准。
