# 维护任务

状态：pending → running → completed | waiting_review | failed → retry | dead_letter | cancelled  

能力：幂等键、重试、取消、Dead Letter、correlation_id  

当前实现为进程内 Job 存储 + Operation Log；与 Canonical 写入解耦，失败不影响 Raw 检索。
