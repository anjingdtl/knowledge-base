# 维护运维 Runbook

## 来源文件更新

```bash
shinehe index D:\docs\file.pdf
# 或事件入口
shinehe maintenance source-event --knowledge-id <id> --event-type updated
```

期望：Impact Plan → R1 保护（stale/unsupported）→ 必要时 Review。

## 冲突审阅

1. `shinehe maintenance reviews`  
2. 对照 Evidence  
3. `shinehe maintenance resolve <review_id> --action confirm|reject|correct`  

## R4 发布预检

```bash
shinehe maintenance evaluate-r4 --job-type publish
# 无 --confirm 时不得执行
```

## 维护中心不可用

- Raw `search` / `ask`（raw_only）仍应可用  
- 检查 `maintenance.enabled` 与日志，无需停检索服务  

## 回滚维护写入

- 通过 Operation Log / Claim Revision  
- 高风险操作用 Authoring + 人工确认
