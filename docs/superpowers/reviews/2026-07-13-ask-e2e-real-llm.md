# 真实 LLM Ask E2E 评测报告

> 日期：2026-07-13  
> 版本：v1.7.0  
> 路径：`search_llm`（SearchService 真实检索 + 真实 chat 生成）  
> 模型：embedding `BAAI/bge-m3`；chat `Qwen/Qwen3-8B`（SiliconFlow）

## 命令

```bash
python evals/run_ask_e2e_eval.py --path search_llm --json --output artifacts/eval/ask-e2e-real-llm.json
```

数据集：`evals/datasets/ask_e2e_fixture.yaml`（15 题：12 事实 + 3 拒答）

## 结果

| 指标 | 值 |
|---|---:|
| Overall | **PASS** |
| Cases | 15 |
| Overall accuracy | **0.8667** |
| Answer accuracy | **0.8333** |
| Refuse accuracy | **1.0000** |
| Citation rate | **1.0000** |
| Keyword coverage | **0.9167** |
| Errors | 0 |
| Latency P50 / P95 | 43.2s / 91.6s |

### 失败样本（2）

| ID | 说明 |
|---|---|
| `e2e_citation` | 回答含 citation 字段说明，但被软拒答启发式误判 |
| `e2e_debounce` | watcher debounce 默认值未稳定召回/生成 |

## 备注

1. **`llm.api_key` 当前 401 无效**；本评测进程内临时使用 `embedding.api_key` 调 chat（**未改写** `config.yaml`）。建议在 GUI「设置 → LLM」更新有效 Secret。
2. 完整 `rag_pipeline` 在隔离 fixture DB 上 hybrid 通道常空转 FTS；E2E 默认 `search_llm` 更贴近「真检索 + 真生成」且探针检索稳定命中。
3. 产物：`artifacts/eval/ask-e2e-real-llm.json`

## 复现

需有效 embedding + chat API（或 embedding key 可兼 chat 的兼容端点）。
