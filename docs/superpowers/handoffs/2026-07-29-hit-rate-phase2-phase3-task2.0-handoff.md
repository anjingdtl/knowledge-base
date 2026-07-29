# Phase 2-3 下班交接单（2026-07-29）

> 本文件已按最新执行报告重写。此前“Task 2.0 约完成 30%”的旧交接内容已失效，以本文件和 `docs/evaluation/mcp-hit-rate-phase2-phase3-report.md` 为准。

## 当前结论

- Task 2.0 工程收口：完成。
- Phase 2 架构：部分完成，已有 ADR、UseCase/Ports 薄层、架构门禁；MCP `retrieval.py` 仍偏厚，业务还未完全下沉。
- Phase 3 质量重建：未完成，仅评分合同与部分地基完成。
- 发布状态：仍 NO-GO。Golden V2 为 `candidates=37, reviewed=0, frozen=0`，所有指标只能标 development/non-formal。
- 未授权未 commit、未 push、未发版。

## 已复核的收尾状态

- `git diff --check`：通过，仅有 Windows CRLF 提示。
- `python tools/report_closure_debt.py --strict`：通过，No residual debt。
- `.venv\Scripts\python.exe -m pytest tests/architecture tests/eval tests/retrieval tests/application tests/test_heartbeat_best_effort.py -q --tb=short`：`188 passed`。
- `data/kb.db` SHA256：`4ba22449794c984f6c1fda3d459574556c71b017efcc8e041bd4da731e737479`，与报告一致。

## 明天优先顺序

1. 先读最新执行报告：`docs/evaluation/mcp-hit-rate-phase2-phase3-report.md`。
2. 复核工作区：`git status --short`，确认保留今天所有未提交改动。
3. 补跑更大分片或全量 pytest，确认报告中的全量分片绿在当前机器仍成立。
4. 进入 Phase 2 剩余建设：拆薄 MCP retrieval adapter，把 search/ask/read 业务继续下沉到 application UseCase，并保持公开契约 snapshot 不漂。
5. 进入 Phase 3 前先跑 development-37 non-formal baseline。不得 formal，不得伪造 reviewer/adjudicator。

## 禁止事项

- 不降低 90% 放行线，不新增 skip/xfail，不删断言，不刷新 Golden 预期。
- 不做 case_id 特判。
- `data/kb.db` 默认只读，任何会改变正式库的操作必须先获得明确授权。
- 未经授权不 commit/push/PR。
