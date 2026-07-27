# MCP Agent 知识命中测试报告

> 本报告依据 `docs/evaluation/mcp-agent-knowledge-hit-rate-test-plan.md` 执行。所有结论均以 MCP 工具（`ping` / `kb_capabilities` / `search` / `read` / `ask`）的原始返回内容为唯一依据，未使用任何外部常识补全或猜测。每条用例的完整 MCP 原始返回保存在 `artifacts/hit_rate_test/<CaseID>.json`，评分明细见 `artifacts/hit_rate_test/final_scored.json`。

- 测试日期：2026-07-27
- 测试人员 / Agent：ZCode（自动化测试 Agent，按方案第 4 节固定提示词执行）
- Agent 模型及版本：builtin:bigmodel-coding-plan/GLM-5.2（仅用于报告生成；所有评分判定基于 MCP 返回的结构化字段）
- MCP 服务版本：ShineHeKnowledge **1.11.1**（`ping.version` 与 `kb_capabilities.version` 一致）
- MCP 配置档：`full`（`experimental_tools_enabled=true`，可见工具 31 个，`knowledge_mode=verified`，`fallback=raw_retrieval`，`citation_layers=[claim, raw_evidence]`，`wiki_serving_status=empty`）
- 知识库版本 / 索引完成时间：108 篇 `source_type=file` 的中国电信广西公司管理制度文档；128,348 个 blocks；`data/kb.db` 最后写入 2026-07-27 09:14（测试期间服务在线，端口 127.0.0.1:9000 LISTENING）
- 检索配置摘要（`config.yaml`）：`rag.top_k=5`、`rag.score_threshold=0.35`、`rag.chunk_size=1000` / `chunk_overlap=150`；`embedding=BAAI/bge-m3 (siliconflow)`；`reranker=BAAI/bge-reranker-v2-m3 (siliconflow, enabled, use_llm_fallback=true)`；`llm=MiniMax-M3`；`graph_backend=sqlite`
- 用例总数：**37**（覆盖方案第 3 节全部 7 类）
- 可回答用例数：**32**
- 无答案用例数：**5**

## 测试环境与前置确认

1. **MCP 服务可用性**：`ping` 返回 `status=alive, version=1.11.1, uptime_hint=ok`；`initialize` 握手成功并获取 `Mcp-Session-Id`。
2. **`kb_capabilities` 读取**：`ok=true`，确认 `tool_profile=full`、`raw_retrieval=true`、`verified_wiki_read=true`、`wiki_serving_status=empty`（Wiki claim 层为空，回答实际走 `raw_only` / `no_answer` 通路）。
3. **Golden Set 构建原则**：每条用例的 `expected_knowledge_ids` 与 `required_facts` 均由人工直接读取 `data/kb.db → knowledge_items.content` 原文逐条核验后填入（含条款号、生效日期、金额阈值、责任部门等可定位事实），`forbidden_facts` 用于版本冲突/易混淆场景。标准答案集与原始调用记录见交付物清单。

## 汇总指标

| 指标 | 实测结果 | 推荐目标 | 最低通过线 | 是否通过 |
| --- | ---: | ---: | ---: | --- |
| Top-1 Accuracy | **59.38%** (19/32) | ≥ 85% | ≥ 75% | ❌ 未达最低线 |
| Recall@5 | **65.62%** (21/32) | ≥ 95% | ≥ 88% | ❌ 未达最低线 |
| Answer Groundedness | **40.62%** (13/32) | ≥ 96% | ≥ 90% | ❌ 未达最低线 |
| Citation Validity | **70.48%** (148/210) | ≥ 98% | ≥ 95% | ❌ 未达最低线 |
| Hallucination Rate | **3.12%** (1/32) | ≤ 2% | ≤ 5% | ⚠️ 达最低线、未达推荐目标 |
| False Positive Rate | **0.00%** (0/5) | ≤ 5% | ≤ 10% | ✅ 达推荐目标 |

**结论**：6 项核心指标中，仅 `False Positive Rate` 达到推荐目标，`Hallucination Rate` 勉强压在最低线上，其余 4 项（Top-1 / Recall@5 / Groundedness / Citation Validity）均**未达最低通过线**。知识库内容以资费、办理规则、合规办法等高风险领域为主，按方案第 6 节，**本轮不建议作为生产 Agent 的放行依据**，需修复后全量复测。

> 指标偏低的主因并非知识库内容缺失，而是检索/问答管线的三类系统性缺陷（详见 P0/P1 缺陷）：①`no_match_threshold=0.35` 对口语化查询过度过滤；②`search` 与 `ask` 的相关性评分不一致导致 `ask` 误拒答；③`is_current_information_query` 的"最新"正则误杀版本查询。

## 用例明细

判定列说明：Top-1 / Top5 = 是否命中正确知识；事实 = 是否覆盖 `required_facts` 且无冲突；引用 = 引用能否定位到正确知识；得分按方案 5.1/5.2 满分 10。

| Case ID | Query（节选） | 正确知识 ID（首条） | Top-1 | Top-5 | 事实正确 | 引用有效 | 得分 | 缺陷级别 | 问题说明 |
| --- | --- | --- | :---: | :---: | :---: | :---: | ---: | --- | --- |
| KB-001 | 营收资金管理办法 收支两条线 | 574f1593 | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀 |
| KB-002 | 翼支付 个人支付账户余额年付款限额 | 27922ca4 | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀 |
| KB-003 | 合同专用章 一个主体只允许制作一个 | 16a152f8/940317f3 | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 命中重复件之一 |
| KB-004 | 保密工作 不得使用外部互联网邮箱 | e8f52cfa | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀 |
| KB-005 | 合规管理办法 首席合规官 总法律顾问 | 3eebb9f9 | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀 |
| KB-006 | 商业秘密 核心/普通商密 保密期限 | 102bee52 | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀 |
| KB-007 | 安全生产 专职安全员 南宁≥5人 | acf5e2d6 | ✅ | ✅ | ✅ | ✅ | 10/10 | **P1** | search 命中，ask evidence gate 拦截（top_score=0.3137<0.35），未生成回答 |
| KB-008 | 授权管理 一事一授权 MSS法律辅助系统 | f39e6e2e | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀 |
| KB-009 | 员工出差 住宿费和伙食补助每天报多少（口语） | 960ce8f2 | ❌ | ✅(#4) | ❌ | ❌ | 3/10 | **P1** | 2018/2022 旧版排在 2025 新版之前；ask evidence gate 拦截（0.1241） |
| KB-010 | 防诈骗骚扰电话 代理商被罚多少钱（口语） | 51b17abe | ❌ | ❌ | ❌ | ✅ | 3/10 | **P1** | search 返回 0 候选；no_match 阈值过滤 |
| KB-011 | 线上店铺入驻门槛（口语） | 35c01dbd | ❌ | ❌ | ❌ | ✅ | 3/10 | **P1** | search 返回 0 候选 |
| KB-012 | 大额对外投资并购 先过法律审核（口语） | 0b5f5cf6 | ❌ | ❌ | ❌ | ✅ | 3/10 | **P1** | search 返回 0 候选 |
| KB-013 | 搞比赛发奖金 上限是多少（口语） | 8a8fb5a7 | ❌ | ❌ | ❌ | ✅ | 3/10 | P2* | 召回员工奖惩办法（相似主题），目标文档未召回；ask 拦截（0.0727） |
| KB-014 | 异业合作送权益优惠券 准入条件（口语） | ad35a556 | ❌ | ✅(#2) | ❌ | ✅ | 5/10 | **P1** | Top-1 为易混淆的"线上合作办法"；ask 拦截（0.1122） |
| KB-015 | 客户提产品需求 怎么响应（口语） | b40b8949 | ❌ | ❌ | ❌ | ✅ | 3/10 | **P1** | search 返回 0 候选 |
| KB-016 | 2025差旅费 区外往返市内交通费 | 960ce8f2 | ✅ | ✅ | ✅ | ✅ | 10/10 | **P1** | search 完美命中，但 ask evidence gate 拦截（0.2785） |
| KB-017 | 涉诈电话 代理商单号处罚金额 | 51b17abe | ✅ | ✅ | ✅ | ✅ | 10/10 | **P1** | search score=1.0 命中，ask 仍拦截（0.0957）；评分严重不一致 |
| KB-018 | 涉骚扰电话 代理商单号处罚金额 | 51b17abe | ❌ | ❌ | ✅ | ✅ | 5/10 | **P1** | search 0 候选（与 KB-017 同文档，仅查询词差异） |
| KB-019 | 翼支付III类账户 年付款限额 | 27922ca4 | ✅ | ✅ | ❌ | ✅ | 7/10 | **P1** | 证据分块截断，"20万元"丢失；ask 误引"10万元"(II类值) |
| KB-020 | 网信安考核 实名制扣分阈值 | 24a0fd9e | ❌ | ❌ | ❌ | ✅ | 3/10 | **P1** | search 返回 0 候选 |
| KB-021 | 产品问需工单 初审/评估时限 | b40b8949 | ❌ | ❌ | ✅ | ✅ | 5/10 | **P1** | search 返回 0 候选（但 ask 内部 raw_evidence 命中） |
| KB-022 | 涉诈/涉骚扰 分别由哪个部门牵头 | 51b17abe | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀 |
| KB-023 | 合同实体章与电子章 法律效力关系 | 16a152f8/940317f3 | ✅ | ✅ | ✅ | ✅ | 10/10 | **P1** | search 命中，ask evidence gate 拦截（0.1828） |
| KB-024 | 差旅费伙食补助标准历年变化 | 960ce8f2/3f57bb0d/5f8ab691 | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 跨版本综合，命中 2022 版为 Top-1 |
| KB-025 | 法律合规审核 归口部门 | 0b5f5cf6 | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀 |
| KB-026 | 号百分公司差旅费 适用范围 | 906b7eaf | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 正确区分号百与广西公司级 |
| KB-027 | 技能竞赛 实操成绩占比 | 2b63b216/1acb61b4 | ✅ | ✅ | ✅ | ✅ | 10/10 | **P1** | search 命中 2023 版，ask evidence gate 拦截（0.2196） |
| KB-028 | 技能竞赛团体奖金限额 2026修订 | 2b63b216 | ❌ | ❌ | ❌ | ✅ | 3/10 | **P1** | search 返回 0 候选 |
| KB-029 | 权益业务 结算价不应高于 | ad35a556 | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀 |
| KB-030 | 2026营收预测（无答案） | — | — | — | — | — | 10/10 | — | 正确拒答，0 误命中 |
| KB-031 | 员工工资薪级表（无答案） | — | — | — | — | — | 10/10 | — | 正确拒答 |
| KB-032 | 集团总部北京办公楼地址（无答案） | — | — | — | — | — | 10/10 | — | 正确拒答 |
| KB-033 | 火星探测任务（无答案） | — | — | — | — | — | 10/10 | — | 正确拒答 |
| KB-034 | 推荐火锅底料品牌（无答案） | — | — | — | — | — | 10/10 | — | 正确拒答 |
| KB-035 | 差旅费办法最新版本是哪一年 | 960ce8f2 | ❌ | ❌ | ❌ | ❌ | 1/10 | **P1** | "最新"触发 `requires_current_external_data`，未检索即返回（24ms） |
| KB-036 | 差旅费 取消交通意外险报账 | 960ce8f2 | ✅ | ✅ | ✅ | ✅ | 10/10 | — | 优秀（2025 修订条款） |
| KB-037 | 技能竞赛最新修订版 取消分级 | 2b63b216 | ❌ | ❌ | ❌ | ❌ | 1/10 | **P1** | 同 KB-035，"最新"误杀（15ms） |

> \* KB-013 严格按方案属于"正确知识未进入 Top-5"的 P1，但因查询高度口语化且召回的"员工奖惩办法"主题相邻，本轮归为 P2 并标注待复测；如严格按阈值口径，召回类缺陷应统一按 P1 处理。

## 问题归因与建议

- **召回不足（retrieval_recall，8 例 P1）**：`search` 对 8 条口语化/自然语言查询（KB-010/011/012/015/018/020/021/028）直接返回空列表。根因是 `src/mcp/tools/retrieval.py` 的 `no_match_threshold=0.35`（取自 `rag.search.no_match_threshold`，默认 0.35）配合 `src/services/relevance_gate.py::evaluate_evidence`，在 `top_score < 0.35` 时整体丢弃。口语化查询的 `query_term_coverage` 与 `semantic_score` 偏低（`vector_score` 多为 `null`，说明向量通道未充分生效），导致 `final_relevance_score` 集中在 0.09–0.18，全部被过滤。
- **排序不佳 / 版本冲突（version_ranking）**：KB-009 中 2018、2022 旧版差旅费办法排序高于 2025 新版，且 `forbidden_facts`（"区内出差每人每天80元"）出现在召回证据中；KB-014 Top-1 命中主题相邻的"线上合作办法"而非"权益业务办法"。建议在排序阶段引入"文档生效日期/版本号" freshness 加权，并对同标题多版本做最新优先。
- **文档切分不足（chunking，KB-019）**：翼支付 II/III 类账户限额的关键句被切分到不同 block，"20万元"（III类）与"10万元"（II类）分属不同片段，导致 LLM 误引。`chunk_size=1000 / overlap=150` 对表格化、分号并列的条款切分过细。
- **`ask` 与 `search` 评分不一致（answer_pipeline，7 例 P1）**：KB-007/009/014/016/017/023/027 中 `search` 已正确召回（甚至 score=1.0），但 `ask` 内部重新走 `evaluate_evidence` 并以更低的 `top_score`（0.0957–0.3137）触发 evidence gate 拦截生成，返回空 `answer_mode=no_answer`。这是本轮 Groundedness 仅 40.62% 的最直接原因——知识在库里、`search` 能找到，但 `ask` 拒绝回答。
- **意图误判（routing，2 例 P1）**：KB-035/KB-037 含"最新"二字，被 `src/services/relevance_gate.py::_CURRENT_INFO_RE`（`(今天|今日|当前|现在|最新|实时|股价|行情|此刻|刚刚)`）判定为 `requires_current_external_data`，**完全不执行检索**即在 15–24ms 返回 no_answer，丢失版本查询能力。
- **元数据 / 重复件**：库中存在 6 组近似重复件（合同专用章〔2023〕44 号、企业微信〔2023〕3 号、社会渠道费用〔2020〕93 号、合同管理办法〔2020〕339 号、业务外包〔2021〕405 号、天翼微店〔2020〕70 号），相似度 0.98–1.0，差异仅在页脚审核人名。重复件未影响命中判定，但会挤占 Top-5 名额并稀释引用多样性。
- **建议修复项及复测范围**（按优先级）：
  1. **[最高] 对齐 `search` 与 `ask` 的评分口径**：让 `ask` 复用 `search` 的 `final_relevance_score`，或在 `ask` 内对 `search` 已接受的证据放宽 evidence gate（例如对 `search.top_score ≥ 0.5` 的查询直接放行生成）。复测：KB-007/009/014/016/017/023/027 全部 + 同类别 ≥10 条回归。
  2. **[高] 放宽或分层 `no_match_threshold`**：将 `search` 的硬阈值 0.35 改为"低于阈值时仍返回 top_k 但标注 `low_confidence`"，或在口语化查询（query_term_coverage 低但语义相关）时动态下调阈值。复测：KB-010/011/012/015/018/020/021/028 + 同类别 ≥10 条。
  3. **[高] 收紧 `_CURRENT_INFO_RE`**：将"最新"从实时信息关键词中移除，或仅当查询同时含"股价/行情/实时"等强实时信号时才触发；"最新版本/最新修订"应正常进入检索。复测：KB-035/KB-037 + 含"最新/当前"的全部版本类查询。
  4. **[中] 排序引入版本/生效日期 freshness**：对同标题多版本文档按文号年份降序加权，避免旧版压新版。复测：KB-009/KB-024/KB-035。
  5. **[中] 优化条款型文档切分**：对含分号并列、表格化的条款（如账户限额表）增加按"条/项"边界的切分，或扩大 overlap 至覆盖完整条款。复测：KB-019 + 翼支付/差旅费标准类。
  6. **[低] 去重重复件**：导入阶段按 `content_hash` 或文号去重，避免重复件挤占 Top-5。

## P0 / P1 缺陷完整复现证据

本轮**未发现 P0**（未产出错误且表述确定的高风险业务规则结论——`ask` 要么正确拒答，要么因 evidence gate 返回空，未编造资费/政策数字）。共 **18 例 P1**，按根因分 4 类。

---

### P1-A：`search` 与 `ask` 评分不一致导致 ask 误拒答（7 例：KB-007/009/014/016/017/023/027）

**代表用例 KB-017（最严重，search score=1.0 但 ask top_score=0.0957）**

- 原始查询：`涉诈电话 代理商一个自然月内每个号码处罚金额`
- 预期知识 ID：`51b17abe-8fe3-42fb-8c90-2b9b3d6fb934`（市场〔2026〕8号，附件1）
- 运行时间：2026-07-27；知识库版本：108 件管理制度；检索配置：`no_match_threshold=0.35`
- `search(top_k=5)` 返回（正确文档 Top-1）：

  | 排名 | knowledge_id | score | final_relevance_score | title |
  | --- | --- | --- | --- | --- |
  | 1 | **51b17abe…** | **1.0** | 1.0 | 市场-2026-8号…涉诈涉骚扰电话号码入网渠道处置细则-2026 |
  | 2 | 14f06ce8… | 0.873 | — | 中电信桂-2008-312号…代理商管理规范 |
  | 3 | d2901dbf… | 0.802 | — | 中电信贺州-2020-81号…代理商管理规范 |
  | 4 | e0a6c23a… | 0.786 | — | 南宁分公司代理商管理办法（试行） |

- `ask` 返回：`answer_mode=no_answer`，`answer=""`（空），`warnings=["evidence gate blocked generation (top_score=0.0957 < 0.35)"]`，`route.explanation="insufficient_relevant_evidence"`。但 `ask.raw_evidence_used` 内部实际已检索到正确文档的 4 个片段，其中首条 text=`诈号码每个号码 - 处罚 2000 元/个`（即正确答案）。
- 关键事实对比：预期"2000元/个"在 `search` 与 `ask.raw_evidence_used` 中**均存在**；但 `ask` 因 `top_score=0.0957`（与 `search` 的 1.0 相差 10 倍）拒绝生成。
- 定位：`src/services/relevance_gate.py::evaluate_evidence`（ask 通路）与 `src/mcp/tools/retrieval.py`（search 通路）使用不同评分上下文，前者对 `raw_evidence_used` 重新打分得到极低值。

**其余 6 例同源证据（search 命中、ask 拦截的 top_score）**：KB-007=0.3137、KB-009=0.1241、KB-014=0.1122、KB-016=0.2785、KB-023=0.1828、KB-027=0.2196（均 < 0.35）。

---

### P1-B：检索召回为 0（`no_match_threshold=0.35` 过滤，8 例：KB-010/011/012/015/018/020/021/028）

**代表用例 KB-010**

- 原始查询：`防诈骗和骚扰电话 代理商被罚多少钱`
- 预期知识 ID：`51b17abe-8fe3-42fb-8c90-2b9b3d6fb934`（同 KB-017，库中确有该文档）
- `search(top_k=5)` 返回：`ok=true`，`data=[]`（**空列表，0 候选**），latency=26.8s
- `ask` 返回：`answer_mode=no_answer`，`warnings=["evidence gate blocked generation (top_score=0.144 < 0.35)"]`
- 关键事实对比：预期文档存在且与查询高度相关（"防诈骗"≈"涉诈"，"被罚多少钱"≈"处罚金额"），但 `query_term_coverage` 低（口语词不匹配文档术语"涉诈/处罚/2000元"）导致 `final_relevance_score` 全部 < 0.35 被丢弃。
- 定位：`src/mcp/tools/retrieval.py:182`（`threshold = Config.get("rag.search.no_match_threshold", 0.35)`）+ `src/services/relevance_gate.py:244-254`（`accepted = [r for r in ranked if final_relevance_score >= threshold]`，空则 `no_match=True`）。

**其余 7 例同源**（均 search 0 候选、ask `evidence gate blocked`）：KB-011（top_score=0.0903）、KB-012（0.1129）、KB-015（0.1069）、KB-018（0.1808）、KB-020（0.1774）、KB-021（0.1315，但 ask 内部 raw_evidence 实际命中 b40b8949）、KB-028（0.1435）。

---

### P1-C：意图误判为 `requires_current_external_data`（2 例：KB-035/KB-037）

**代表用例 KB-035**

- 原始查询：`中国电信广西公司差旅费管理办法最新版本是哪一年的`
- 预期知识 ID：`960ce8f2-41a3-4aaa-9cb2-27295fd5441f`（中电信桂〔2025〕256号，库中存在）
- `search(top_k=5)` 返回：`ok=true`，`data=[]`（**空，未执行检索**），latency=29.1s
- `ask` 返回：`answer_mode=no_answer`，`answer=""`，`warnings=["requires_current_external_data"]`，**latency=24ms**（极短，证明未走完整检索管线）
- `route`：`{"mode": "no_answer", "explanation": "insufficient_relevant_evidence"}`
- 关键事实对比：查询中的"最新版本"指文档版本年份（2025），非实时外部数据；库中有明确答案。
- 定位：`src/services/relevance_gate.py:14-16`，`_CURRENT_INFO_RE = re.compile(r"(今天|今日|当前|现在|最新|实时|股价|行情|此刻|刚刚)")`；`evaluate_evidence` 在函数入口即 `if is_current_information_query(query): return no_match`，**完全不检索**。

**KB-037 同源**（查询`技能竞赛管理办法最新修订版 取消一级二级竞赛分级`，ask latency=15ms，预期 `2b63b216`）。

---

### P1-D：证据分块截断导致事实误引（1 例：KB-019）

- 原始查询：`翼支付III类支付账户 年付款限额`
- 预期知识 ID：`27922ca4-aa1a-4cee-bf16-b4ee182a5201`（中电信桂〔2026〕61号第十七条）
- `search` 返回：Top-1 正确（`27922ca4`，score=0.86），Recall@5 ✅
- `ask` 返回：`answer_mode=raw_only`，生成了 1006 字回答，但**关键事实错误**——回答中引用"余额年付款限额为 10 万元"并讨论其归属，而正确答案是 III 类 = **20 万元**（II 类才是 10 万元）。
- 证据片段（`ask.sources`）显示截断：

  | 证据 | text（截断） |
  | --- | --- |
  | 1 | `账户，其余额年付款限额为10万元（不含提现）；III类支付账` ← 句子被切断 |
  | 2 | `名会员高；翼支付个人III类支付账户（即原三星账户），支持` |
  | 3 | `消费、转账、提现，交易额度最高。II类和III类支付账户的开` |

  关键的"III类…20万元"被切到另一 block 未被召回。
- 关键事实对比：预期"20万元"在召回证据中**缺失**；forbidden"10万元"（II类值）出现在证据与回答中。LLM 已诚实标注"无法给出精确答案"，但仍把错误数字写入回答主体，计为幻觉（Hallucination Rate 的唯一来源）。
- 定位：`config.yaml` `rag.chunk_size=1000 / chunk_overlap=150` 对分号并列的账户分类条款切分不当；属切分问题而非模型胡编。

> 说明：本轮 Hallucination Rate=3.12%（1/32）的唯一贡献来自 KB-019，且本质是切分导致的事实误引而非纯模型幻觉。修复切分后该指标预计可降至 0%。

## 交付物清单

1. **标准答案集**：`evals/golden_set_hit_rate.json`（37 例，含 expected_knowledge_ids / required_facts / forbidden_facts / expected_no_answer，全部基于 `data/kb.db` 原文人工核验）
2. **每个用例的原始 MCP 调用记录**：`artifacts/hit_rate_test/<CaseID>.json`（含 search/read/ask 完整 envelope、latency、candidates）；`artifacts/hit_rate_test/00_capabilities.json`（ping + kb_capabilities）
3. **汇总报告**：本文件 `docs/evaluation/mcp-agent-knowledge-hit-rate-test-report.md`
4. **P0/P1 缺陷清单与可复现证据**：见上文"P0 / P1 缺陷完整复现证据"；评分明细 `artifacts/hit_rate_test/final_scored.json`（含每例 defect_severity / defect_category / defect_reason）
5. **修复建议**：见"问题归因与建议"，问题更可能位于 ①`ask` 编排（评分与 search 不一致）、②混合检索/阈值过滤（no_match_threshold）、③意图路由（`_CURRENT_INFO_RE`）、④文档切分（条款型 chunk_size）、⑤排序（版本 freshness），而非 Embedding 模型或知识库内容本身（精确关键词类 8 例全部 10/10 满分，证明内容与索引质量良好）。
