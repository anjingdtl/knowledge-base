# Raw Retrieval 评测

```bash
python evals/run_retrieval_eval.py --all
python evals/run_retrieval_eval.py --all --fake-embedding
```

指标：Recall@5、MRR、nDCG@10、No-Answer、Citation Location Completeness、延迟。

`--fake-embedding` 结果**不得**宣传为真实模型效果。
