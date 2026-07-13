# Phase 7 阶段报告：Hybrid Eval

> 日期：2026-07-13  
> Spec：§14 / Phase 7

## 交付

| 项 | 路径 |
|---|---|
| 黄金集生成 | `evals/hybrid_eval/cases.py`（175 例 ≥150） |
| 三路打分 | `evals/hybrid_eval/scoring.py` |
| CLI | `evals/run_hybrid_eval.py` |
| 测试 | `tests/test_hybrid_eval.py` |
| 文档 | `docs/evaluation/hybrid-knowledge.md` |
| 样例报告 | `artifacts/eval/hybrid-report.md` |

## 类别覆盖

单文档事实、中文缩写、跨文档、概念总结、数值单位、地区/条件、冲突、stale 时效、无答案、PDF/DOCX/Excel 定位、unsupported 守卫、Wiki fallback；通信行业样本 ≥30。

## 门禁结果

```text
cases=175 overall=PASS
raw=1.0 wiki=1.0 hybrid=1.0
stale_serving_rate=0 unsupported_serving_rate=0
citation_correctness=1.0 conflict_detection_recall=1.0
```

## 说明

离线确定性评测（无 embedding/LLM）。真实模型对照可另接 retrieval/wiki eval。
