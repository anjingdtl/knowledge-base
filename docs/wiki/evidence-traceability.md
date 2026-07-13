# Evidence 可追溯性

每个被采用的 Claim 必须返回至少一个原始 Evidence：

```json
{
  "knowledge_id": "doc_...",
  "block_id": "block_...",
  "path": "...",
  "location": {},
  "evidence_stance": "supports"
}
```

`read claim_id=...` 返回 Claim、状态、Relations、Evidence 与当前有效性。
