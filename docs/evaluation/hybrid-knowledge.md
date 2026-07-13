# Hybrid 知识评测

## 命令

```bash
python evals/run_hybrid_eval.py
python evals/run_hybrid_eval.py --markdown --output artifacts/eval/hybrid-report.md
python evals/run_hybrid_eval.py --strict
```

## 方法

- **离线确定性**：≥150 条合成黄金集（通信 + 冲突 + 时效 + 定位 + 无答案）  
- 三路：Raw Only / Wiki Only / Hybrid Verified  
- **不**使用 embedding/LLM（fake 与真实模型需另跑 retrieval/wiki eval）  

## 门禁（Spec §14.4）

- Hybrid 正确率 ≥ Raw  
- Stale / Unsupported Serving Rate = 0  
- Citation Correctness ≥ 0.95  
- Conflict Detection Recall ≥ 0.90  
- Raw Fallback Success ≈ 1.0  

## 相关

- Raw：`python evals/run_retrieval_eval.py --all`  
- Wiki evolution：`python evals/run_knowledge_evolution_eval.py`  
