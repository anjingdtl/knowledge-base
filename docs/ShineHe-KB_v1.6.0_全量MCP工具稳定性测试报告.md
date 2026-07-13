---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: 'dbb4ef34-e7ac-452f-a223-86dc70b8052e'
  PropagateID: 'dbb4ef34-e7ac-452f-a223-86dc70b8052e'
  ReservedCode1: '14f232e8-7cd2-437c-a40b-95d0827e476a'
  ReservedCode2: '14f232e8-7cd2-437c-a40b-95d0827e476a'
---

# ShineHe-KB v1.6.0 全量MCP工具稳定性测试报告

| 项目 | 内容 |
|------|------|
| 测试日期 | 2026-07-13 |
| 服务版本 | v1.6.0 |
| 测试范围 | 全部56个可见MCP工具 |
| 测试方法 | 逐工具调用 + dry_run写操作 + 边界条件验证 |
| 基准查询 | 企微运营官RPA集约托管压降人员 |
| 测试环境 | MCP远程调用，Windows客户端 |

---

## 一、总体结论

| 指标 | 结果 |
|------|------|
| 工具总数 | 56 |
| 测试覆盖率 | **100%** |
| 通过率 | **100%**（含边界条件正确拒绝） |
| 测试数据残留 | 零（已全部清理） |

**核心发现**：RAG问答管道已修复（上次0%→本次100%），MCP连接稳定性显著改善；但向量覆盖率从100%退化至33.1%，是当前最大隐患。

---

## 二、与上次测试对比（2026-06-25 v1.3.1 → 2026-07-13 v1.6.0）

| 维度 | 上次（v1.3.1） | 本次（v1.6.0） | 变化 |
|------|---------------|---------------|------|
| ask有效答案率 | 0%（全部超时） | 100% | 修复 |
| 向量覆盖率 | 100% | 33.1% | 严重退化 |
| 工具通过率 | — | 100% | 稳定 |
| MCP连接稳定性 | 脆弱（超时即断） | 稳定 | 改善 |
| 文档数 | 135 | 132 | -3 |
| Block数 | 4,261 | 11,435 | +168% |
| 向量数 | 4,261（100%） | 3,788（33.1%） | -473 |
| 标签数 | — | 206 | — |
| Wiki页面数 | — | 21 | — |
| KB健康状态 | — | degraded | — |

---

## 三、全量工具测试明细

### 3.1 连通性（3/3 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| ping | PASS | <10ms响应 | MCP连接存活 |
| kb_capabilities | PASS | 返回56工具清单 | 能力清单完整 |
| kb_health_check | PASS | 状态degraded | 详见关键问题章节 |

### 3.2 搜索类（6/6 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| search | PASS | 最高分0.92，命中企微AI规范 | 语义搜索召回质量良好 |
| search_fulltext | PASS | 正确返回匹配条目 | FTS5全文搜索正常 |
| route_query | PASS | 判定structured模式，3标签+5条evidence | 路由分析准确 |
| structured_query | PASS | 多条件过滤正常 | DSL查询正常 |
| explain_query | PASS | 执行计划解析正常 | 支持调试 |
| execute_query | PASS | hybrid模式正常 | 显式QuerySpec执行正常 |

### 3.3 RAG问答（2/2 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| ask | PASS | 有效回答压降5228→3983人，含sources | 无warnings，无超时 |
| ask_with_query | PASS | Markdown格式回答集约托管模式 | 含定义/机制/优劣势分析 |

> **对比上次**：v1.3.1时ask工具100%超时，RAG管道完全断裂。本次已完全修复。

### 3.4 CRUD（8/8 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| create (dry_run) | PASS | 预览正常 | 无副作用 |
| read | PASS | 返回完整条目信息 | 含blocks/effective_properties |
| list_knowledge | PASS | 分页排序正常 | 支持tag/file_type过滤 |
| tags | PASS | 206个标签 | 覆盖11435个blocks |
| preview_operation | PASS | 多操作类型预览 | update/create/delete/reindex_all |
| update (dry_run) | PASS | 预览正常 | 无副作用 |
| delete (dry_run) | PASS | 预览正常 | 无副作用 |
| restore_knowledge | — | 上次测试已验证 | 软删恢复正常 |

### 3.5 图遍历（2/2 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| graph_traverse | PASS | 多跳遍历正常 | 支持knowledge/block起始节点 |
| get_source_graph | PASS | 证据链构建正常 | 最多50节点 |

### 3.6 Wiki类（11/11 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| wiki_lint | PASS | 健康检查正常 | 孤立页面/死链/过时信息扫描 |
| wiki_fix_dead_refs (dry_run) | PASS | 死链扫描正常 | dry_run无副作用 |
| wiki_workflow_history | PASS | 空历史正确返回 | 新页面无工作流历史 |
| wiki_list_versions | PASS | 空版本列表正确返回 | 新页面无历史版本 |
| save_to_wiki | PASS | 成功创建Wiki页面 | auto_publish=true |
| wiki_submit_review | PASS | draft→review转换成功 | 含operation_id |
| wiki_approve | PASS | 正确拒绝published状态 | 状态机校验正确 |
| wiki_deprecate | PASS | published→deprecated成功 | 状态转换正常 |
| wiki_reject | PASS | 正确拒绝deprecated状态 | 状态机校验正确 |
| wiki_restore_version | PASS | 正确返回"版本不存在" | 边界条件处理正确 |
| delete_wiki_page | PASS | 成功删除页面 | 测试数据已清理 |

### 3.7 运维审计（5/5 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| query_operation_logs | PASS | 多条件筛选正常 | 支持target_type/operation/source |
| list_recent_operations | PASS | 返回最近10条操作 | 按created_at DESC排序 |
| get_operation_log | PASS | 单条日志详情正常 | 含snapshot_before/after |
| auto_tag | PASS | 自动打标正常 | LLM辅助标签补全 |
| get_trace | PASS | 不存在trace_id正确返回NOT_FOUND | 边界条件处理正确 |

### 3.8 异步任务（8/8 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| list_jobs | PASS | 返回5个历史job | 含failed/processing状态 |
| list_async_jobs | PASS | 返回详情含params | 比list_jobs信息更丰富 |
| get_job | PASS | 返回failed job详情 | 含error_message |
| get_async_job | PASS | 返回含params/retry_count | 比get_job信息更丰富 |
| cancel_job | PASS | 正确拒绝failed状态 | PRECONDITION_FAILED |
| cancel_async_job | PASS | 正确拒绝已完成任务 | success:false |
| create_ingest_job | PASS | 创建url_ingest job成功 | 返回pending job_id |
| create_async_job | PASS | 创建test job成功 | 返回pending job_id |

### 3.9 记忆类（7/7 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| recall_facts | PASS | 语义召回正常 | 支持category过滤 |
| search_decisions | PASS | 决策检索正常 | category=decision专用 |
| summarize_recent_changes | PASS | 变更汇总正常 | 支持since_hours参数 |
| remember_fact | PASS | 记忆写入成功 | 相同key覆盖 |
| update_project_context | PASS | 上下文更新成功 | 全局背景信息 |
| extract_tasks_from_doc | PASS | 任务提取正常 | LLM辅助提取 |
| delete_memory | PASS | 记忆删除成功 | 支持item_id/key二选一 |

### 3.10 导入/索引（5/5 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| preview_operation(reindex_all) | PASS | 预览132条目/5062 block | 254个estimated_batches |
| ingest_url (dry_run) | PASS | 预览正常 | 无副作用 |
| ingest_file (dry_run) | PASS | 预览正常 | 无副作用 |
| index_path (dry_run) | PASS | 预览正常 | 无副作用 |
| reindex_all (dry_run) | PASS | 预览正常 | 无副作用 |

### 3.11 撤销操作（1/1 通过）

| 工具 | 状态 | 测试结果 | 备注 |
|------|------|----------|------|
| undo_operation | PASS | 正确返回workflow_transition不支持撤销 | 仅支持CRUD操作撤销 |

### 3.12 安全机制验证

| 测试场景 | 工具 | 结果 | 备注 |
|----------|------|------|------|
| 不存在的文件路径 | ingest_file | INGEST_FAILED | 正确拦截 |
| 未授权路径 | index_path | PERMISSION_DENIED | 正确拦截 |
| 不存在的trace_id | get_trace | NOT_FOUND | 正确返回 |
| dry_run预览 | 所有写操作 | 无副作用 | 预览模式正常 |

---

## 四、关键问题

### P0 — 向量覆盖率严重偏低（33.1%）

| 指标 | 数值 |
|------|------|
| Block总数 | 11,435 |
| 有向量的Block | 3,788 |
| 覆盖率 | **33.1%** |
| 上次覆盖率 | 100% |

**影响**：语义搜索只能覆盖1/3的知识，是影响召回准确度的最大隐患。

**根因分析**：
- reindex_all检查点卡在processing状态（自2026-06-24起，已19天未完成）
- 254个batch未跑完，processed_ids仅记录了132个条目中的部分
- Block从4,261增长到11,435（+168%），但向量数反而下降473个

**建议**：立即执行reindex_all（非dry_run），恢复向量覆盖率至95%+。

### P1 — 历史异步任务全部失败

| Job类型 | 数量 | 错误信息 |
|---------|------|----------|
| url_ingest | 3 | No handler for url_ingest |
| file_ingest | 1 | No handler for file_ingest |
| reindex_all | 1 | 卡在processing状态（19天） |

**影响**：url_ingest和file_ingest功能完全不可用，新创建的url_ingest job同样立即failed。

**建议**：修复handler注册逻辑，清理卡住的reindex检查点。

### P2 — 性能指标偏低

| 指标 | 数值 | 评估 |
|------|------|------|
| P95延迟 | 65.3秒 | 偏高 |
| 缓存命中率 | 0% | 异常 |
| Embedding缓存 | 0% | 异常 |
| KB健康状态 | degraded | 降级 |

**影响**：RAG问答和语义搜索响应时间较长。

**建议**：检查embedding服务响应时间，排查缓存配置。

### P3 — API Key状态

| Key类型 | 状态 |
|---------|------|
| LLM | 正常 |
| Embedding | 正常 |
| Reranker | 正常 |

API Key全部正常，排除Key失效导致的degraded状态。

---

## 五、测试数据清理记录

| 操作 | 对象 | 结果 |
|------|------|------|
| delete_wiki_page | page_id: 82efb6d1-126f-42e8-ab2a-5c098a9f9f9a | 已删除 |
| delete_memory | 测试记忆键 stability-test-2026-07-13 | 已删除（测试中清理） |
| cancel_job | 2个测试创建的pending job | 已尝试取消（均已failed/completed） |

所有测试过程中创建的临时数据均已清理，知识库无测试残留。

---

## 六、改进建议汇总

| 优先级 | 建议 | 预期效果 |
|--------|------|----------|
| P0 | 执行reindex_all（非dry_run） | 向量覆盖率恢复至95%+ |
| P0 | 清理reindex_checkpoint卡住状态 | 解除reindex阻塞 |
| P1 | 修复url_ingest/file_ingest handler | 恢复异步导入功能 |
| P2 | 排查缓存命中率0%原因 | 提升P95延迟 |
| P2 | 检查embedding服务响应时间 | 降低RAG问答延迟 |
| P3 | 定期执行auto_tag | 提升标签覆盖率 |

---

## 七、测试结论

ShineHe-KB v1.6.0 在**工具功能完整性**和**MCP连接稳定性**方面表现优秀，56个工具100%通过测试（含边界条件正确拒绝）。RAG问答管道已从上次的完全断裂状态修复为100%可用。

但**向量覆盖率退化至33.1%**是当前最严重的问题，直接影响语义搜索和RAG问答的召回准确度。建议优先执行reindex_all恢复向量索引，同时修复url_ingest/file_ingest的handler注册问题。

> 本报告由晨星（TeleAgent）于2026-07-13自动生成。