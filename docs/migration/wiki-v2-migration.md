# Canonical Wiki V2 迁移指南

将 A 轨（SQLite `wiki_pages`）与 B 轨（文件系统 `wiki/*.md`）合并进 Canonical Store（`wiki/**/*.md` + `claims/*.yaml` + registry）。

## 前提

- 已完成 Phase 0–5（Canonical 模型、Primary 写路径、依赖失效传播）
- 建议先备份整个项目目录
- **不会**在 apply 后自动把 `wiki.canonical_v2.mode` 切到 `primary`

## 命令

```bash
# 1. 只读规划（零写入）
shinehe wiki migrate-v2

# 2. 执行迁移（全局 lock + 备份 + 事务写入 draft claims/pages）
shinehe wiki migrate-v2 --apply

# 3. 校验 provenance / 引用
shinehe wiki validate
shinehe wiki validate --strict

# 4. 回滚到某次备份时间戳
shinehe wiki migrate-v2 --rollback 20260713T120000
```

备份目录默认：`<project>/backups/wiki-v2-<timestamp>/`，内含 `wiki/` 与 `migration-report.json`。

## 匹配规则

1. 已有 `page_id` 且在 registry → 跳过
2. 相同 `source_ids` + 规范化标题 → 合并
3. 相同标题 + 内容 bigram Jaccard ≥ 0.85 → 合并
4. aliases 命中 → 合并
5. 同名但内容差异大 → **conflict**，禁止自动合并

## Claim 生成

- 解析 body 中 `## Facts` 下的 bullet
- 有 `source_ids` / `knowledge_id` → Evidence `page_only`，status=`draft`（**不**自动 active）
- 无来源 → status=`unsupported`，计入 untraceable

## 用户反馈

```bash
shinehe wiki claims list
shinehe wiki claims show <claim_id>
shinehe wiki claims review <claim_id> --action confirm
shinehe wiki claims review <claim_id> --action reject
shinehe wiki claims review <claim_id> --action correct --correction "修正文案"
shinehe wiki claims review <claim_id> --action needs_review --note "待核"
```

反馈只改 Claim 状态/文案，写 operation log，**不修改 Raw Source**。

## Cutover 建议

全部满足后再人工启用 primary：

1. `migrate-v2` dry-run conflicts=0
2. `validate --strict` 无 error
3. 抽样审阅 draft claims
4. 核心检索 eval 不低于基线
5. 手动设置 `wiki.canonical_v2.mode: primary`

## 评测

```bash
python evals/run_knowledge_evolution_eval.py
```
