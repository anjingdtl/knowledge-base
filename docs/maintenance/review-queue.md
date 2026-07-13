# 审阅队列

- 类型：new_claim / correction / conflict / stale_rebuild / publish …  
- 动作：confirm / reject / correct / needs_review / defer  
- 须对照 Claim 与原始 Evidence  
- 可接 `WikiFeedbackService` 写 Claim 状态  

CLI：`shinehe maintenance reviews` / `resolve`  
API：`GET/POST /api/maintenance/reviews`
