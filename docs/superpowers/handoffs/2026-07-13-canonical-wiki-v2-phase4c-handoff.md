# Canonical Wiki V2 Phase 4C Primary 交接单

> Closure update（2026-07-13）：Phase 4C 已通过最终门禁并完成 review；后续从
> `PROGRESS.md` 和 `docs/superpowers/reviews/2026-07-13-phase4c-primary-review.md` 接续
> Phase 5 规划。本交接单下方保留的是验收前的历史快照。

> 日期：2026-07-13
> 分支：`feature/wiki-v2-phase4a-shadow`
> 交接状态：验收前历史快照。
> 权威总方案：`docs/superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md`

## 结论先行

不要进入 Phase 5。Phase 4C 的 Canonical 主写实现和直接写守卫收缩已经落盘，但完整
验证被中断，没有本阶段可用的全量测试、eval 或 review 证据。先在当前 HEAD 完成审查和
所有门禁；只有通过后才可更新 `PROGRESS.md` 的 phase 状态并创建 phase 汇总提交。

## 当前仓库状态

- 当前 HEAD：`d6ccf02 Route wiki compiler writes through canonical repository`
- 本交接写入前工作树：clean。
- 远程：`origin https://github.com/anjingdtl/knowledge-base.git`；本交接未执行 push。
- 上一次被中断的 `pytest -q` 残留的两个 Python 子进程已停止；没有可采信的最终输出。
- 源码版本仍为 `1.4.0`，本阶段未变更版本号。

## 已完成的 Phase 4C 实现

| 范围 | 已提交结果 | 主要提交 |
|---|---|---|
| Primary ingest 编排 | `KnowledgeWorkflowService` 在 primary 模式仅运行 `WikiPrimaryWorkflow`，不再先运行 legacy compiler。 | `1028f1a` |
| Query save | `WikiWriteService` primary 模式通过 Repository 保存 Canonical Page，并保留 `sqlite_page_id` 兼容字段。 | `1028f1a` |
| Legacy compiler 输入 | entity/source/index/log/query 的旧 writer 已降为 suggestion 或 prepared payload。 | `8026112` 至 `c1aa2e2` |
| API / workflow | API CRUD 与 workflow status/restore 改经 Repository + Projection。 | `03663f6`、`80b447c` |
| Lint | lint score、异常标记、重复页 deprecation 改经 Projection / canonical helper；DI fallback 已两轮复核。 | `83c33f2`、`2cf77eb` |
| Compiler | query save、source cleanup、新建/更新页、dead-reference repair 的 legacy 页面写入改经 canonical helper。 | `d6ccf02` |
| Guard | `ALLOWED_DIRECT_WRITES`、`GUARDED`、`OPEN_WRITE_GUARDED` 已清空。 | `d6ccf02` |

计划的逐项状态在
`docs/superpowers/plans/2026-07-09-canonical-wiki-v2-phase4c-primary-plan.md`：Task 1-4I
已完成；Task 5（phase verification、full suite、review、汇总 commit）保持未完成。

## 已有验证证据

以下结果可以作为回归线索，不可替代最终门禁：

```text
pytest tests/test_canonical_write_guards.py tests/test_wiki_compiler_canonical.py \
       tests/test_save_to_wiki_params.py tests/test_wiki_compiler_primary_adapter.py \
       tests/test_wiki_workflow_canonical.py tests/test_wiki_projection.py -q
32 passed
```

Lint 改造阶段还曾完成：

```text
pytest tests/test_canonical_write_guards.py tests/test_wiki_lint.py \
       tests/test_wiki_lint_canonical.py tests/test_wiki_workflow_canonical.py \
       tests/test_wiki_projection.py -q
34 passed
ruff check src tests evals tools scripts
mypy src tools
```

但是以上都发生在部分后续改动之前。`d6ccf02` 后的 `pytest -q` 被中断，故没有本阶段
全量结果。不要复制 Phase 4B 的 `1425 passed` 到 Phase 4C。

## 必须先做的工作

1. 阅读总方案、Phase 4C plan、本文和 `PROGRESS.md` 顶部的 2026-07-13 快照。
2. 运行并记录 Phase 4C 定向门禁：

   ```bash
   pytest tests/test_wiki_write_service.py tests/test_knowledge_workflow.py \
          tests/test_wiki_compiler_primary_adapter.py tests/test_canonical_write_guards.py \
          tests/test_wiki_compiler_canonical.py tests/test_wiki_lint.py \
          tests/test_wiki_lint_canonical.py tests/test_wiki_workflow_canonical.py \
          tests/test_wiki_projection.py -q
   ruff check src tests evals tools scripts
   mypy src tools
   python evals/run_retrieval_eval.py --all
   python evals/run_wiki_eval.py
   pytest -q
   ```

3. 对当前 HEAD 做独立代码 review，重点审查：primary 是否有 legacy double-write、Projection 是否始终与注入 DB 配对、`content` 回填是否只服务 legacy compatibility、guard 是否真的覆盖所有目标入口。
4. 处理 review / verification 发现的问题后，重复相应验证。
5. 全部通过后，创建 `docs/superpowers/reviews/2026-07-09-phase4c-primary-review.md`，更新 `PROGRESS.md`，再按计划创建汇总提交：

   ```bash
   git commit -m "refactor(wiki-v2): switch primary canonical write path"
   ```

## 需要明确审查的提交范围

`d6ccf02` 不仅包含 compiler 实现和测试，还意外包含以下运行产物：

```text
wiki/_meta/pages.json
wiki/_meta/redirects.json
wiki/syntheses/*.md
```

共 10 个 `wiki/` 文件。这些文件没有在 `.gitignore` 中被排除。不要擅自重写历史；在
Phase 4C review 中判断它们是否属于应版本控制的 fixture/样例。若不是，使用**独立的
后续提交**移除，并在 review 记录理由与验证影响。

## 已知风险和禁止事项

- C2 黄金集仍有 5 个 xfailed（单位、型号、地区、否定、强度词）；保持保守
  `unresolved` 策略，不能为追求自动合并而放宽。
- C3 的 publish 中断场景仍可能留下 claims 目录孤儿；Phase 5 的依赖/重编译设计要
  显式收敛，但不能抢跑。
- Canonical Page 的 source-of-truth 是 Repository；SQLite Projection 只能重建或维护
  legacy read model，不能成为独立写源。
- Phase 4C 未验收前，不启用 Phase 5 的 source update/delete 失效传播，不扩展自动发布，
  不删除 legacy fallback。

## 阅读顺序

1. `PROGRESS.md` 顶部交接快照。
2. `docs/superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md`。
3. `docs/superpowers/plans/2026-07-09-canonical-wiki-v2-phase4c-primary-plan.md`。
4. `docs/superpowers/reviews/2026-07-09-phase4b-canary-review.md`。
5. `docs/architecture/wiki-v2-claim-merge-contract.md`。
6. `evals/wiki_v2/README.md`。
