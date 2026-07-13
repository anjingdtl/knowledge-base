# Wiki Serving Gate

`WikiServingGate` 是 Claim 能否进入回答的**唯一门禁**。

## 默认允许

- status = `active`  
- 存在可解析 supports Evidence（knowledge_id + block_id）  
- Evidence 非 stale（可配置）  
- 校验通过、非 review-required  

## 排除

draft / unsupported / retracted / 证据缺失 / hash 不匹配  

## disclose_only

冲突或诊断场景可侧信道保留，**不**作为主结论静默胜出。

入口：`SearchService.list_servable_wiki_claims` / `repo.list_servable_claims(gate=...)`。
