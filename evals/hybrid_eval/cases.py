"""Build the Verified Hybrid golden set (Spec §14.2, min 150 / target 165).

Cases are deterministic offline fixtures: each provides synthetic raw / claim
result rows so Raw / Wiki / Hybrid modes can be scored without embedding/LLM.
"""
from __future__ import annotations

from typing import Any


def _raw(
    kid: str,
    bid: str,
    text: str,
    *,
    title: str = "",
    score: float = 0.7,
    path: str = "",
) -> dict[str, Any]:
    return {
        "source": "knowledge",
        "candidate_type": "raw_block",
        "knowledge_id": kid,
        "block_id": bid,
        "title": title or kid,
        "text": text,
        "score": score,
        "source_layer": "evidence",
        "citation": {
            "knowledge_id": kid,
            "block_id": bid,
            "path": path or f"fixtures/{kid}.md",
            "text": text,
        },
    }


def _claim(
    cid: str,
    statement: str,
    *,
    kid: str,
    bid: str,
    status: str = "active",
    stale: bool = False,
    score: float = 0.85,
    freshness: str | None = None,
) -> dict[str, Any]:
    fr = freshness or ("stale_partial" if stale else "current")
    return {
        "source": "verified_claim",
        "candidate_type": "claim",
        "claim_id": cid,
        "text": statement,
        "status": status,
        "freshness": fr,
        "score": score,
        "knowledge_id": kid,
        "block_id": bid,
        "source_layer": "canonical",
        "eligible": status == "active" and not stale,
        "evidence": [
            {
                "knowledge_id": kid,
                "block_id": bid,
                "stance": "supports",
                "stale": stale,
                "path": f"fixtures/{kid}.md",
                "ok": not stale and status == "active",
            },
        ],
    }


def _case(
    cid: str,
    category: str,
    query: str,
    *,
    raw: list[dict] | None = None,
    claims: list[dict] | None = None,
    disclose: list[dict] | None = None,
    expected: dict[str, Any] | None = None,
    telecom: bool = False,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    exp = {
        "answer_mode": "hybrid_verified",
        "conflict": False,
        "no_answer": False,
        "must_have_evidence": True,
        "forbid_stale_in_answer": True,
        "forbid_unsupported_status": True,
        "correct_claim_ids": [],
        "correct_knowledge_ids": [],
        "hybrid_at_least_raw": True,
        "prefer_raw": False,
        **(expected or {}),
    }
    return {
        "id": cid,
        "category": category,
        "query": query,
        "telecom": telecom,
        "tags": tags or [],
        "raw_results": list(raw or []),
        "claim_results": list(claims or []),
        "disclose_claims": list(disclose or []),
        "expected": exp,
    }


def build_hybrid_cases() -> list[dict[str, Any]]:
    """Return >= 165 deterministic hybrid eval cases."""
    cases: list[dict[str, Any]] = []

    # ── 1. 单文档事实 (25) ── telecom heavy
    facts = [
        ("FTTR 主网关上行峰值速率是多少？", "FTTR 主网关上行峰值 1Gbps", "fttr_spec", "b_uplink"),
        ("OLT 设备典型下联口数量是多少？", "OLT 典型提供 16 个 PON 口", "olt_manual", "b_pon"),
        ("ONU 注册失败常见原因是什么？", "ONU 注册失败常见于光衰过大或 SN 未授权", "onu_faq", "b_reg"),
        ("PON 分光比常用配置是什么？", "PON 常用分光比为 1:64", "pon_guide", "b_split"),
        ("5G 基站 BBU 与 AAU 如何连接？", "BBU 与 AAU 通过 eCPRI/光纤前传连接", "5g_arch", "b_fronthaul"),
        ("视频双录最短时长要求？", "视频双录业务最短录制时长不少于 3 分钟", "dual_record", "b_dur"),
        ("营业厅号卡激活需要什么证件？", "号卡激活需本人有效身份证件原件", "hall_service", "b_id"),
        ("套餐变更生效时间？", "套餐变更默认次月 1 日生效", "tariff_rules", "b_eff"),
        ("故障工单超时标准是多少？", "家宽故障工单 4 小时响应超时", "ticket_sla", "b_sla"),
        ("光衰合格门限是多少？", "家宽光衰合格门限 ≤ 28dB", "optical_budget", "b_db"),
        ("SQLite 默认使用什么模式？", "SQLite uses WAL mode for local indexing", "architecture", "b_wal"),
        ("RRF 默认 k 值是多少？", "RRF fusion constant k=60", "architecture", "b_rrf"),
        ("embedding 维度默认多少？", "embedding model outputs 1024 dimensions", "architecture", "b_dim"),
        ("MCP 默认 tool_profile 是什么？", "default tool_profile is extended", "config_ref", "b_prof"),
        ("如何开启 rerank？", "set reranker.enabled true in config", "troubleshooting", "b_rr"),
        ("知识库数据存在哪里？", "all data stays in local SQLite database", "architecture", "b_local"),
        ("watch 的防抖默认多少？", "file watcher debounce_ms default 500", "config_ref", "b_deb"),
        ("FTS5 用于什么？", "FTS5 provides keyword full-text search", "architecture", "b_fts"),
        ("Parent-Child 检索作用？", "parent-child expands block context for generation", "architecture", "b_pc"),
        ("citation 必须包含什么？", "citation includes knowledge_id block_id and location", "api_guide", "b_cit"),
        ("向量覆盖率不足怎么修？", "run vector coverage maintenance repair", "troubleshooting", "b_vec"),
        ("异步导入大文件用什么？", "use create_ingest_job for large imports", "api_guide", "b_job"),
        ("Doctor 检查什么？", "doctor checks knowledge mode and serving health", "config_ref", "b_doc"),
        ("Graph 后端默认是什么？", "graph_backend provider defaults to sqlite", "config_ref", "b_g"),
        ("HTTP MCP 默认端口？", "MCP HTTP default port is 9000", "config_ref", "b_port"),
    ]
    for i, (q, stmt, kid, bid) in enumerate(facts, 1):
        cases.append(_case(
            f"sf_{i:03d}",
            "single_fact",
            q,
            raw=[_raw(kid, bid, stmt, title=kid, score=0.72)],
            claims=[_claim(f"c_sf_{i}", stmt, kid=kid, bid=bid)],
            expected={
                "answer_mode": "hybrid_verified",
                "correct_claim_ids": [f"c_sf_{i}"],
                "correct_knowledge_ids": [kid],
            },
            telecom=i <= 10,
            tags=["telecom"] if i <= 10 else ["system"],
        ))

    # ── 2. 中文专有名词 / 缩写 (15) ──
    abbr = [
        ("什么是 FTTR？", "FTTR 即 Fiber to The Room 光纤到房间", "fttr_def"),
        ("什么是 PON？", "PON 是无源光网络 Passive Optical Network", "pon_def"),
        ("OLT 全称是什么？", "OLT 全称 Optical Line Terminal 光线路终端", "olt_def"),
        ("ONU 全称是什么？", "ONU 全称 Optical Network Unit 光网络单元", "onu_def"),
        ("什么是 eCPRI？", "eCPRI 是增强通用公共无线电接口", "ecpri_def"),
        ("什么是 SLA？", "SLA 是服务等级协议 Service Level Agreement", "sla_def"),
        ("什么是 RRF？", "RRF 是 Reciprocal Rank Fusion 倒数排名融合", "rrf_def"),
        ("什么是 FTS5？", "FTS5 是 SQLite 全文检索扩展", "fts_def"),
        ("什么是 MCP？", "MCP 是 Model Context Protocol 模型上下文协议", "mcp_def"),
        ("什么是 RAG？", "RAG 是 Retrieval Augmented Generation 检索增强生成", "rag_def"),
        ("什么是 BBU？", "BBU 是基带处理单元 Baseband Unit", "bbu_def"),
        ("什么是 AAU？", "AAU 是有源天线单元 Active Antenna Unit", "aau_def"),
        ("什么是 SN 码？", "SN 是设备序列号 Serial Number", "sn_def"),
        ("什么是 dB？", "dB 是分贝 用于表示光功率衰减", "db_def"),
        ("什么是 Serving Gate？", "Serving Gate 判定 Claim 是否可进入回答", "gate_def"),
    ]
    for i, (q, stmt, kid) in enumerate(abbr, 1):
        cases.append(_case(
            f"abbr_{i:03d}",
            "zh_abbreviation",
            q,
            raw=[_raw(kid, f"b_{kid}", stmt, score=0.65)],
            claims=[_claim(f"c_ab_{i}", stmt, kid=kid, bid=f"b_{kid}", score=0.9)],
            expected={
                "answer_mode": "hybrid_verified",
                "correct_claim_ids": [f"c_ab_{i}"],
                "correct_knowledge_ids": [kid],
            },
            telecom=i <= 6,
        ))

    # ── 3. 跨文档综合 (25) ──
    for i in range(1, 26):
        k1, k2 = f"doc_a_{i}", f"doc_b_{i}"
        t1 = f"主题{i}A：核心参数为 {i * 10}Mbps"
        t2 = f"主题{i}B：部署约束见规范第 {i} 章"
        q = f"综合说明主题{i}的参数与部署约束"
        claim_stmt = f"主题{i}参数 {i * 10}Mbps 且遵循第 {i} 章部署约束"
        cases.append(_case(
            f"xdoc_{i:03d}",
            "cross_document",
            q,
            raw=[
                _raw(k1, f"ba_{i}", t1, score=0.7),
                _raw(k2, f"bb_{i}", t2, score=0.68),
            ],
            claims=[_claim(f"c_xd_{i}", claim_stmt, kid=k1, bid=f"ba_{i}", score=0.88)],
            expected={
                "answer_mode": "hybrid_verified",
                "correct_claim_ids": [f"c_xd_{i}"],
                "correct_knowledge_ids": [k1, k2],
            },
            telecom=i % 2 == 0,
            tags=["cross_doc"],
        ))

    # ── 4. 概念定义 / 实体总结 (15) ──
    concepts = [
        ("总结 Verified 模式", "Verified 模式默认读取已验证 Claim 且关闭 Agent 写"),
        ("总结 Authoring 模式", "Authoring 模式允许 Wiki 维护但默认不自动发布"),
        ("总结 evidence_only", "evidence_only 仅原始文档检索用于降级与对照"),
        ("Canonical Store 是什么", "Canonical Store 保存 Page Claim Evidence 与关系"),
        ("Evidence Layer 是什么", "Evidence Layer 以原始文档与 Block 为最终证据"),
        ("Maintenance Center 做什么", "维护中心编排保护性维护与审阅不写第二事实库"),
        ("Claim 状态有哪些", "Claim 状态含 active draft disputed unsupported retracted"),
        ("什么是 R1 保护动作", "R1 保护动作可自动标记 stale 与降级 unsupported"),
        ("什么是 R4 动作", "R4 含发布删除迁移必须人工确认"),
        ("Hybrid 融合用什么", "Hybrid 使用 RRF 融合 Claim 与 Raw 通道"),
        ("冲突披露规则", "冲突时并列披露双方证据不得静默选边"),
        ("Serving 失败怎么办", "Wiki 不可用时自动降级为 Raw Retrieval"),
        ("引用契约要求", "主结论必须可追溯到 claim 与原始 evidence"),
        ("write_policy 默认", "verified 默认 mcp.write_policy 为 disabled"),
        ("Projection 是什么", "Projection 是 Canonical 的可读投影不是第二真相"),
    ]
    for i, (q, stmt) in enumerate(concepts, 1):
        kid = f"concept_{i}"
        cases.append(_case(
            f"concept_{i:03d}",
            "concept_summary",
            q,
            raw=[_raw(kid, f"bc_{i}", stmt, score=0.6)],
            claims=[_claim(f"c_co_{i}", stmt, kid=kid, bid=f"bc_{i}")],
            expected={
                "answer_mode": "hybrid_verified",
                "correct_claim_ids": [f"c_co_{i}"],
                "correct_knowledge_ids": [kid],
            },
        ))

    # ── 5. 精确数值 / 单位 / 型号 (15) ──
    nums = [
        ("X-100 光猫最大并发会话？", "型号 X-100 最大并发会话 256", "x100", False),
        ("X-200 支持 Wi-Fi 频段？", "型号 X-200 支持 2.4G 与 5G 双频", "x200", False),
        ("上行承诺带宽是多少？", "上行承诺带宽 100Mbps 峰值可达 1Gbps", "bw", False),
        ("时延 SLA 是多少？", "接入时延 SLA ≤ 20ms", "lat", False),
        ("丢包率门限？", "丢包率门限 ≤ 0.1%", "loss", False),
        ("分光器插入损耗典型值？", "1:64 分光器插入损耗约 20dB", "il", False),
        ("ONU 功耗典型值？", "ONU 典型功耗 8W", "power", False),
        ("OLT 机框支持槽位？", "OLT 机框支持 16 个业务槽位", "slot", False),
        ("双录分辨率要求？", "双录分辨率不低于 720p", "res", False),
        ("套餐流量包含多少 GB？", "基础套餐包含 30GB 国内流量", "flow", False),
        ("向量维度是？", "向量维度固定 1024", "emb", False),
        ("top_k 默认？", "检索 top_k 默认 8", "topk", False),
        ("ask 总超时默认？", "ask total_timeout 默认 90 秒", "timeout", False),
        ("chunk_size 默认？", "chunk_size 默认 1200", "chunk", False),
        ("max_workers 默认？", "jobs max_workers 默认 2", "workers", False),
    ]
    for i, (q, stmt, kid, _) in enumerate(nums, 1):
        cases.append(_case(
            f"num_{i:03d}",
            "numeric_unit",
            q,
            raw=[_raw(kid, f"bn_{i}", stmt, score=0.75)],
            claims=[_claim(f"c_num_{i}", stmt, kid=kid, bid=f"bn_{i}")],
            expected={
                "answer_mode": "hybrid_verified",
                "correct_claim_ids": [f"c_num_{i}"],
                "correct_knowledge_ids": [kid],
                "preserve_numbers": True,
            },
            telecom=i <= 10,
            tags=["numeric"],
        ))

    # ── 6. 地区 / 时间 / 条件限定 (10) ──
    scopes = [
        ("广东省现行 FTTR 安装规范？", "广东省 FTTR 安装要求独立电箱与接地", "gd_fttr", "广东"),
        ("北京市 5G 室分验收标准？", "北京室分验收需覆盖率 ≥ 95%", "bj_5g", "北京"),
        ("仅适用于政企专线的规则？", "政企专线 SLA 不适用于公众家宽", "gov_line", "政企"),
        ("工作日生效的变更规则？", "工作日 9-18 点提交的变更当日生效", "biz_hour", "工作日"),
        ("截至 2025 年资费政策？", "截至 2025 年底老套餐可保留一年", "tariff_2025", "2025"),
        ("夜间维护窗口是何时？", "核心网维护窗口为每日 01:00-05:00", "maint_win", "夜间"),
        ("仅室内覆盖场景？", "该天线方案仅适用于室内覆盖", "indoor", "室内"),
        ("农村宽带建设标准？", "农村宽带最低接入速率 100Mbps", "rural", "农村"),
        ("测试环境配置？", "测试环境禁用 auto_publish", "test_env", "测试"),
        ("生产环境 write_policy？", "生产 verified 必须 write_policy=disabled", "prod_wp", "生产"),
    ]
    for i, (q, stmt, kid, scope) in enumerate(scopes, 1):
        cases.append(_case(
            f"scope_{i:03d}",
            "scope_condition",
            q,
            raw=[_raw(kid, f"bs_{i}", stmt, score=0.7)],
            claims=[_claim(f"c_sc_{i}", stmt, kid=kid, bid=f"bs_{i}")],
            expected={
                "answer_mode": "hybrid_verified",
                "correct_claim_ids": [f"c_sc_{i}"],
                "correct_knowledge_ids": [kid],
                "scope_token": scope,
            },
            telecom=i <= 8,
            tags=["scope"],
        ))

    # ── 7. 冲突来源 (15) ──
    for i in range(1, 16):
        va, vb = 100 * i, 200 * i
        ca = _claim(
            f"c_cf_a_{i}",
            f"指标峰值 {va}Mbps",
            kid=f"src_a_{i}",
            bid=f"ba_cf_{i}",
            score=0.9,
        )
        cb = _claim(
            f"c_cf_b_{i}",
            f"指标峰值 {vb}Mbps",
            kid=f"src_b_{i}",
            bid=f"bb_cf_{i}",
            score=0.88,
        )
        cases.append(_case(
            f"conflict_{i:03d}",
            "conflict",
            f"指标峰值到底是多少（样本{i}）",
            raw=[
                _raw(f"src_a_{i}", f"ba_cf_{i}", ca["text"], score=0.7),
                _raw(f"src_b_{i}", f"bb_cf_{i}", cb["text"], score=0.7),
            ],
            claims=[ca, cb],
            expected={
                "answer_mode": "conflict_disclosure",
                "conflict": True,
                "must_have_evidence": True,
                "correct_claim_ids": [f"c_cf_a_{i}", f"c_cf_b_{i}"],
            },
            telecom=True,
            tags=["conflict"],
        ))

    # ── 8. 文件更新 / 过期 stale (10) ──
    for i in range(1, 11):
        stale_c = _claim(
            f"c_stale_{i}",
            f"现行指标 {50 * i}Mbps（旧版）",
            kid=f"old_doc_{i}",
            bid=f"bst_{i}",
            stale=True,
            status="active",
            score=0.95,
        )
        fresh_raw = _raw(
            f"new_doc_{i}",
            f"bnw_{i}",
            f"当前最新指标 {80 * i}Mbps（新版文档）",
            score=0.8,
            title=f"new_doc_{i}",
        )
        cases.append(_case(
            f"stale_{i:03d}",
            "freshness_stale",
            f"当前最新指标是多少（样本{i}）",
            raw=[fresh_raw],
            claims=[stale_c],
            expected={
                # stale excluded → raw_only preferred
                "answer_mode": "raw_only",
                "forbid_stale_in_answer": True,
                "correct_knowledge_ids": [f"new_doc_{i}"],
                "prefer_raw": True,
            },
            telecom=True,
            tags=["stale", "freshness"],
        ))

    # ── 9. 无答案 (15) ──
    for i in range(1, 16):
        cases.append(_case(
            f"na_{i:03d}",
            "no_answer",
            f"完全不存在的主题 ZZZ-UNKNOWN-{i} 的秘密参数是多少？",
            raw=[],
            claims=[],
            expected={
                "answer_mode": "no_answer",
                "no_answer": True,
                "must_have_evidence": False,
                "correct_claim_ids": [],
                "correct_knowledge_ids": [],
            },
            tags=["no_answer"],
        ))

    # ── 10. PDF / DOCX / Excel 定位 (20) ──
    for i in range(1, 21):
        kind = ["pdf", "docx", "xlsx"][i % 3]
        kid = f"loc_{kind}_{i}"
        bid = f"bloc_{i}"
        text = f"{kind.upper()} 文档第 {i} 处关键条款内容"
        path = f"fixtures/{kid}.{kind if kind != 'xlsx' else 'xlsx'}"
        r = _raw(kid, bid, text, path=path, score=0.77)
        r["citation"]["location"] = (
            {"page": i} if kind == "pdf"
            else {"heading_path": [f"Section {i}"], "paragraph_index": i}
            if kind == "docx"
            else {"sheet": f"Sheet{i % 3 + 1}"}
        )
        c = _claim(f"c_loc_{i}", text, kid=kid, bid=bid)
        cases.append(_case(
            f"loc_{i:03d}",
            "location_media",
            f"定位 {kind} 文档中第 {i} 条款",
            raw=[r],
            claims=[c],
            expected={
                "answer_mode": "hybrid_verified",
                "correct_claim_ids": [f"c_loc_{i}"],
                "correct_knowledge_ids": [kid],
                "require_location": True,
            },
            tags=["location", kind],
        ))

    # ── Extra: unsupported must not serve (5) — beyond 165 for safety ──
    for i in range(1, 6):
        bad = _claim(
            f"c_unsup_{i}",
            f"未支持断言 {i}",
            kid=f"u_{i}",
            bid=f"bu_{i}",
            status="unsupported",
            score=0.99,
        )
        cases.append(_case(
            f"unsup_{i:03d}",
            "unsupported_guard",
            f"未支持断言 {i} 是否可信？",
            raw=[_raw(f"u_{i}", f"bu_{i}", f"原始片段 {i}", score=0.5)],
            claims=[bad],
            expected={
                "answer_mode": "raw_only",
                "forbid_unsupported_status": True,
                "correct_knowledge_ids": [f"u_{i}"],
                "prefer_raw": True,
            },
            tags=["unsupported"],
        ))

    # ── Wiki failure fallback (5) ──
    for i in range(1, 6):
        cases.append(_case(
            f"fallback_{i:03d}",
            "wiki_fallback",
            f"Wiki 故障时仍应回答问题 {i}",
            raw=[_raw(f"fb_{i}", f"bfb_{i}", f"原始兜底内容 {i}", score=0.8)],
            claims=[],  # wiki empty
            expected={
                "answer_mode": "raw_only",
                "correct_knowledge_ids": [f"fb_{i}"],
                "prefer_raw": True,
                "must_have_evidence": True,
            },
            tags=["fallback"],
        ))

    assert len(cases) >= 150, len(cases)
    return cases


def category_counts(cases: list[dict[str, Any]] | None = None) -> dict[str, int]:
    cases = cases or build_hybrid_cases()
    out: dict[str, int] = {}
    for c in cases:
        out[c["category"]] = out.get(c["category"], 0) + 1
    return out
