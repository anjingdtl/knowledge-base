# Evidence-only 模式

仅原始证据检索：降级、调试、Raw 对照评测。

## 行为

- 不读 Verified Claim
- 不启用 Hybrid 融合
- 维护中心默认 observe（只观测）

```bash
shinehe init --mode evidence-only --local --path D:\docs
```

兼容：`mode: legacy` → `evidence_only`。
