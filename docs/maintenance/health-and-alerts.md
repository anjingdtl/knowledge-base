# 健康快照

`GET /api/maintenance/health` / `shinehe maintenance health`：

- Claim 状态计数、servable 数量  
- stale evidence、开放审阅、失败 Job  
- automation_level / knowledge_mode  

告警关注：Serving stale、保护失败、Projection drift（后续可接指标导出）。
