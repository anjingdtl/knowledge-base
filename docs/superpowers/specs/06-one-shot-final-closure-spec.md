# ShineHeKnowledge 一次性彻底收尾 SPEC

> **建议文件名：** `06-one-shot-final-closure-spec.md`  
> **目标版本：** `v1.10.2`  
> **基线版本：** `v1.10.1`  
> **执行对象：** Codex、Claude Code、OpenAI Codex CLI 或其他开发 Agent  
> **执行原则：** 单分支、单批次、单发布提交；完成后冻结 v1.x 架构治理，不再继续大型重构

---

# 1. 目标

本 Spec 用于一次性处理 v1.10.1 竣工复核后剩余的明确问题，并正式结束本轮可维护性专项。

完成后必须达到：

- MCP 能力自描述字段准确；
- Search、Ask、Wiki、MCP 契约全部进入独立 CI 门禁；
- Ruff 覆盖整个仓库，包括 Alembic；
- 前后端版本元数据一致；
- 远端 CI 有明确的全绿证据；
- v1.x 架构治理进入冻结状态；
- 未来技术债只登记，不再以“当前项目未完成”为由继续重构。

---

# 2. 本轮唯一允许修改的范围

只允许处理以下五项。

## FIX-1：接通 `hidden_by_policy`

当前 `src/mcp/registration.py` 已维护：

```python
RegistrationState.hidden_by_policy
```

但 `kb_capabilities()` 仍返回固定空数组。

修改：

```text
src/mcp/tools/retrieval.py
tests/
```

目标行为：

```python
"hidden_by_policy": sorted(state.hidden_by_policy) if state else []
```

要求：

- Bootstrap 已运行时返回真实 Policy 隐藏工具；
- Bootstrap 未运行时返回 `[]`；
- 不改变字段名、字段类型或其他 MCP Payload；
- 增加至少三个测试：无隐藏项、有隐藏项、未 Bootstrap。

## FIX-2：补全 Contract Gate

修改：

```text
.github/workflows/ci.yml
```

Contract Gate 必须显式运行：

```bash
pytest \
  tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py \
  tests/test_mcp_contract.py \
  -q --tb=short
```

要求：

- 不允许 `continue-on-error`；
- 不允许 `|| true`；
- 不允许把 Wiki 契约仅依赖全量 Test Job 间接覆盖；
- Contract Gate 名称或日志中明确包含 Wiki。

## FIX-3：Ruff 覆盖整个仓库

将 CI 中：

```bash
ruff check src tests evals tools scripts
```

改为：

```bash
ruff check .
```

要求：

- Alembic Revision 必须纳入 Ruff；
- 不得通过排除 `alembic/` 规避问题；
- 对确有兼容 re-export 的导入使用明确 `# noqa: F401` 或 `__all__`；
- 禁止无审查执行大范围 `ruff --fix` 后直接提交；
- 所有自动修复必须检查是否误删兼容导出、注册副作用导入和测试 Patch Surface。

## FIX-4：统一前端版本元数据

统一：

```text
client/package.json
client/package-lock.json
```

目标版本：

```text
1.10.2
```

要求：

- `package.json.version` 与 Lockfile 根包版本一致；
- 不修改依赖版本；
- 不重新生成无关 Lockfile 内容；
- `npm ci` 和 `npm run build` 必须通过。

本 Spec 默认采用与后端统一的 `1.10.2`。

## FIX-5：发布与远端验收闭环

完成前四项后：

- 将 `src/version.py` 升级为 `1.10.2`；
- 更新 README、README_zh、PROGRESS；
- 创建 `docs/release/v1.10.2-release-notes.md`；
- 创建本轮最终验收报告；
- 推送后确认 master 的远端 CI 全绿；
- 创建 `v1.10.2` Git Tag；
- 创建 GitHub Release，并引用 Release Notes。

除非用户明确禁止发布动作，否则 Agent 应完成 Tag 和 Release；如权限不足，必须在报告中明确列为唯一人工步骤。

---

# 3. 明确禁止扩大范围

本轮不得修改：

- Retrieval 算法、排序、召回、RRF、Rerank；
- SearchService 架构；
- Wiki Claim、Evidence、Serving Gate；
- Answer 组装逻辑；
- MCP 工具名、参数和公开返回结构；
- Database Schema；
- Alembic Revision；
- Container Provider 结构；
- Repository 架构；
- `Database._instance` 兼容入口；
- `get_active_container()` 兼容入口；
- MCP 直接 SQL 的全面重构；
- Wiki Projection Provider 归属；
- Legacy Alias 删除；
- `src/mcp_server.py` 兼容层删除。

发现上述问题时，只允许登记到 v2.0 技术债列表，不得在本轮修改。

---

# 4. 执行流程

必须按以下顺序执行：

```text
STEP 0 基线确认
  ↓
STEP 1 FIX-1 hidden_by_policy
  ↓
STEP 2 FIX-2 + FIX-3 CI 门禁
  ↓
STEP 3 FIX-4 版本元数据
  ↓
STEP 4 全量验收
  ↓
STEP 5 版本发布
  ↓
STEP 6 v1.x 架构冻结
```

---

# 5. STEP 0：基线确认

开始前必须确认：

- 当前分支基于 v1.10.1；
- 工作区干净；
- `python tools/report_closure_debt.py --strict` 通过；
- 全量测试基线没有未知失败；
- 当前 Alembic Head 不变；
- 不存在未提交数据库或测试临时产物。

若基线已有失败，先停止并报告，不得把既有失败混入本轮。

---

# 6. STEP 1：修复 MCP 能力自描述

实施 FIX-1。

目标测试建议：

```text
tests/mcp/test_kb_capabilities_policy_state.py
```

最低断言：

```python
assert payload["hidden_by_policy"] == sorted(expected_hidden)
assert isinstance(payload["hidden_by_policy"], list)
```

同时确认以下字段语义互不混淆：

- `visible_tools`：实际可见工具；
- `registered_tools`：已注册工具；
- `hidden_groups`：未启用的工具组；
- `hidden_by_policy`：因 Policy 被隐藏的具体工具。

Commit：

```text
fix(mcp): expose real hidden-by-policy capability state
```

---

# 7. STEP 2：修复 CI 门禁

同时完成 FIX-2 和 FIX-3。

目标：

```text
Contract Gate = Search + Ask + Wiki + MCP
Lint Gate = ruff check .
```

增加轻量 Workflow 静态测试：

```text
tests/architecture/test_ci_workflow_contracts.py
```

断言 `.github/workflows/ci.yml` 包含：

```text
tests/test_wiki_serving_contract.py
ruff check .
python tools/report_closure_debt.py --strict
```

并断言不存在：

```text
continue-on-error: true
|| true
```

Commit：

```text
ci: complete contract and repository-wide lint gates
```

---

# 8. STEP 3：统一版本元数据

实施 FIX-4。

修改：

```text
src/version.py
client/package.json
client/package-lock.json
README.md
README_zh.md
PROGRESS.md
```

版本统一为：

```text
1.10.2
```

增加版本一致性测试：

```text
tests/architecture/test_version_consistency.py
```

断言：

- `src/version.py`；
- README Badge；
- `client/package.json`；
- `client/package-lock.json` 根包版本；

全部一致。

Commit：

```text
chore(release): align project version metadata at 1.10.2
```

---

# 9. STEP 4：一次性最终验收

必须运行以下命令，不允许省略：

```bash
python tools/report_closure_debt.py --strict
ruff check .
mypy src tools
pytest tests/ -q
```

契约专项：

```bash
pytest \
  tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py \
  tests/test_mcp_contract.py \
  -q
```

迁移专项：

```bash
pytest \
  tests/migrations/ \
  tests/storage/ \
  tests/test_alembic_baseline.py \
  -q
```

评测：

```bash
python evals/run_retrieval_eval.py \
  --all \
  --fake-embedding \
  --baseline evals/baselines/local.json \
  --max-regression 0.05

python evals/run_hybrid_eval.py --strict
```

前端：

```bash
cd client
npm ci
npm run build
```

不得使用：

```text
pytest.skip 隐藏失败
|| true
continue-on-error
临时注释断言
降低 Eval 门槛
扩大白名单
```

---

# 10. STEP 5：最终发布

创建：

```text
docs/release/v1.10.2-release-notes.md
docs/superpowers/reviews/one-shot-final-closure-acceptance.md
```

验收报告必须包含：

- 修改文件；
- Commit SHA；
- 所有测试结果；
- Contract Gate 结果；
- Ruff 全仓库结果；
- Retrieval/Hybrid Eval；
- 前端构建结果；
- 版本一致性结果；
- 远端 CI 链接或 Run ID；
- Tag 和 Release 状态；
- 已知问题；
- 是否允许关闭专项。

最终发布 Commit：

```text
release: v1.10.2 one-shot final closure
```

然后：

```text
push master
等待远端 CI 全绿
创建 tag v1.10.2
创建 GitHub Release
```

任何远端 CI 失败都必须修复并重新运行完整受影响门禁后，才能创建 Tag。

---

# 11. v1.x 架构冻结规则

发布 v1.10.2 后，新增：

```text
docs/architecture/v1-maintainability-freeze.md
```

必须写明：

## 11.1 v1.x 只允许处理

- 功能 Bug；
- 安全问题；
- 数据损坏风险；
- 性能严重回退；
- CI 回归；
- 用户升级阻塞；
- 明确的公开契约错误。

## 11.2 v1.x 不再处理

- 为了“更纯粹”而继续拆分模块；
- 为了减少文件行数而重构；
- 兼容层提前删除；
- Provider 重新归属；
- SearchService 进一步变薄；
- MCP SQL 全量下沉；
- 非必要的 Repository 重构；
- 没有用户影响的架构美化。

## 11.3 v2.0 技术债清单

统一登记但不执行：

```text
Database._instance compatibility
get_active_container compatibility
execute_primary_legacy shim
legacy MCP aliases
src/mcp_server.py compatibility re-export
SearchService private retrieval helpers
MCP residual direct SQL
Wiki Projection provider ownership
MCP large domain file further split
```

这些项目不得再被描述为 v1.10.2 “未完成”。

---

# 12. 硬停止条件

以下条件全部通过后，本专项必须关闭：

```text
hidden_by_policy 返回真实值
Wiki Contract 进入独立 CI Gate
Ruff 覆盖整个仓库
前后端版本元数据一致
Architecture strict 通过
全量 pytest 通过
Ruff 通过
MyPy 通过
Migration 测试通过
Search/Ask/Wiki/MCP 契约通过
Retrieval Eval 通过
Hybrid Eval 通过
Frontend Build 通过
远端 CI 全绿
v1.10.2 Tag 和 Release 完成
v1.x 架构冻结文档完成
```

完成后：

> 不得再发起新的 v1.x 可维护性专项复核，不得因为存在 v2.0 技术债而判定当前项目未竣工。

只有出现以下情况，才允许重新打开专项：

- 生产数据损坏；
- 安全漏洞；
- 公共契约破坏；
- 迁移失败；
- CI 无法阻止已知回归；
- Retrieval/Wiki 关键指标实际下降。

---

# 13. Agent 执行报告格式

```markdown
# v1.10.2 一次性最终收尾报告

## 基线
- Base SHA：
- Branch：
- Working tree：

## 修改
- FIX-1：
- FIX-2：
- FIX-3：
- FIX-4：
- FIX-5：

## 明确未修改
- Retrieval：
- Wiki：
- Database Schema：
- Container：
- Public MCP Contract：

## 验收
- Debt strict：
- Ruff：
- MyPy：
- Full pytest：
- Contract：
- Migration：
- Retrieval Eval：
- Hybrid Eval：
- Frontend Build：
- Version Consistency：
- Remote CI：

## 发布
- Release Commit：
- Tag：
- GitHub Release：

## v2.0 技术债登记
- ...

## 最终结论
- 是否关闭 v1.x 可维护性专项：YES / NO
- 原因：
```

---

# 14. 完成定义

本 Spec 完成后，ShineHeKnowledge v1.10.2 必须被正式认定为：

> **v1.x 可维护性、迁移治理和发布一致性全部收尾完成。**

后续工作默认回归产品功能开发。任何进一步架构优化必须进入 v2.0 规划，不再作为 v1.x 竣工条件。
