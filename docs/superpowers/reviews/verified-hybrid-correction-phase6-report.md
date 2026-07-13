# Verified Hybrid 收束纠偏 Phase 6 报告

> 日期：2026-07-13
> 状态：通过，可进入 Phase 7
> 提交：待提交

## 行为变化

- 新增 60 题 release fixture：Claim-benefit 20、Raw-preferred 15、冲突/时效 10、fallback guard 10、no-answer 5；其中通信领域 30 题。
- 新 runner 对每题以同源 Raw Block、已验证/已发布 Claim 分别运行真实 `SearchService` 与 `VerifiedAnswerService`；报告 Raw/Hybrid accuracy、Claim-benefit lift、answer mode 与 verified claim 数。
- runner 对数据集不足、重复 ID、缺固定 Evidence/Claim、全部未出现 verified claim、Hybrid 低于 Raw 或 Claim-benefit 提升不足 5pp 都会失败。

## 验证

```text
python evals/run_verified_hybrid_release_eval.py --strict --json
total=60; raw_accuracy=0.6667; hybrid_accuracy=1.0000;
claim_benefit_lift=1.0000; verified_claim_count=60; overall_pass=true

python -m pytest tests/test_hybrid_eval.py tests/test_verified_hybrid_release_eval.py -q
9 passed

python -m ruff check evals/verified_hybrid_release evals/run_verified_hybrid_release_eval.py tests/test_verified_hybrid_release_eval.py
All checks passed
```

## 兼容与风险

该 release runner 是不依赖外部 embedding/LLM 的同源服务链路 fixture，专门验证融合逻辑与证据门禁。真实 provider/model 的受控 release 复测及其签名摘要仍将在 Phase 8 最终评审中单独记录，不能用此确定性结果替代外部模型质量声明。
