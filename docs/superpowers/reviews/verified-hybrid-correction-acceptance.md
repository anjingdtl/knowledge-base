# Verified Hybrid 收束纠偏验收

> 日期：2026-07-14
> 范围：本地确定性验收；远端 CI/Docker 结果必须在 push 后追加。

| 场景 | 证据 |
|---|---|
| A 新用户 Verified / Raw 可用 / 无写 | `test_knowledge_settings.py`、Windows smoke |
| B 合格 Claim 进入 Hybrid | `test_verified_answer.py`、release eval |
| C Wiki 故障 Raw fallback | Hybrid/release eval fallback 类别 |
| D Authoring 需确认、auto publish 关闭 | `test_knowledge_settings.py`、Serving validation tests |
| E 冲突披露和 Evidence | `test_verified_answer.py` |
| F 来源更新 fail-closed + durable Job/Review | maintenance repo/worker/scheduler tests |
| G `wiki_first` 兼容 Authoring + Hybrid | `test_knowledge_settings.py`、config migration tests |
| H 自动闭环、Review、Validator、Parity、Publish | `test_wiki_serving_validation_migrator.py`、maintenance tests |

执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verified-hybrid-acceptance.ps1
```

这份验收不替代真实 provider 的质量评测，也不把本机缺少 Docker 的情况记为通过。
