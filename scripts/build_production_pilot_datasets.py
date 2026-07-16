"""Build rule-assisted production-pilot annotation candidates.

This command is deliberately incapable of publishing ground truth.  Its output
must pass independent primary and secondary review before the freeze command
will place any row in the formal evaluation directory.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "kb.db"
OUT_DIR = ROOT / "tests" / "eval" / "datasets" / "candidates"
ART = ROOT / "artifacts" / "foundation-three-fixes"
SHA = hashlib.sha256(DB.read_bytes()).hexdigest()[:16]
CORPUS_SNAPSHOT = f"kb.db:{SHA}"


@dataclass
class Doc:
    id: str
    title: str
    content: str
    file_type: str
    source_path: str


def load_docs() -> list[Doc]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT id, title, content, COALESCE(file_type,'') AS file_type,
               COALESCE(source_path,'') AS source_path
        FROM knowledge_items
        WHERE deleted_at IS NULL OR deleted_at = ''
        """
    ).fetchall()
    con.close()
    return [
        Doc(
            id=r["id"],
            title=r["title"] or "",
            content=r["content"] or "",
            file_type=r["file_type"] or "",
            source_path=r["source_path"] or "",
        )
        for r in rows
    ]


def contains_all(text: str, terms: list[str]) -> bool:
    return all(t in text for t in terms)


def contains_any(text: str, terms: list[str]) -> bool:
    return any(t in text for t in terms)


def find_relevant(
    docs: list[Doc],
    *,
    title_must: list[str] | None = None,
    title_any: list[str] | None = None,
    content_must: list[str] | None = None,
    content_any: list[str] | None = None,
    content_must_any_group: list[list[str]] | None = None,
    forbid_title: list[str] | None = None,
    exclude_test_artifacts: bool = True,
    max_expected: int = 5,
) -> tuple[list[str], list[str], list[str]]:
    """Return (expected_ids, acceptable_ids, forbidden_ids) after content checks."""
    expected: list[str] = []
    acceptable: list[str] = []
    weak: list[str] = []

    for d in docs:
        if exclude_test_artifacts and ("测试工件" in d.title or "请勿保留" in d.title):
            continue
        title = d.title
        body = d.content
        blob = f"{title}\n{body}"

        if forbid_title and contains_any(title, forbid_title):
            continue

        has_title_filter = bool(title_must or title_any)
        has_content_filter = bool(content_must or content_any or content_must_any_group)

        title_ok = True
        if title_must and not contains_all(title, title_must):
            title_ok = False
        if title_any and not contains_any(title, title_any):
            title_ok = False

        content_ok = True
        if content_must and not contains_all(body, content_must):
            content_ok = False
        if content_any and not contains_any(body, content_any):
            content_ok = False
        if content_must_any_group:
            for group in content_must_any_group:
                if not contains_any(body, group):
                    content_ok = False
                    break

        # Strong: title + content evidence
        if has_title_filter and title_ok and content_ok:
            expected.append(d.id)
        elif (not has_title_filter) and has_content_filter and content_ok:
            expected.append(d.id)
        elif has_title_filter and title_ok and not content_ok:
            # Title-only without content confirmation → acceptable at most
            if content_any and contains_any(body, content_any or []):
                expected.append(d.id)
            else:
                acceptable.append(d.id)
        elif content_ok and content_any and contains_any(blob, content_any or []):
            weak.append(d.id)

    # Dedup preserve order
    def uniq(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    expected = uniq(expected)[:max_expected]
    acceptable = [x for x in uniq(acceptable) if x not in expected][:max_expected]
    # Forbidden: random distractors with no match — filled later if needed
    return expected, acceptable, []


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def candidate_metadata(generation_rule: dict | None = None) -> dict:
    return {
        "generated_by": "scripts/build_production_pilot_datasets.py",
        "generation_rule": generation_rule or {},
        "corpus_snapshot_sha": CORPUS_SNAPSHOT,
        "annotation_source": "rule_assisted_candidate",
        "human_review_status": "pending",
    }


def ret_row(
    rid: str,
    query: str,
    category: str,
    expected: list[str],
    acceptable: list[str] | None = None,
    forbidden: list[str] | None = None,
    difficulty: str = "medium",
    notes: str = "",
) -> dict | None:
    if not expected:
        return None
    return {
        "id": rid,
        "query": query,
        "category": category,
        "candidate_expected_ids": expected,
        "candidate_acceptable_ids": acceptable or [],
        "candidate_forbidden_ids": forbidden or [],
        "difficulty": difficulty,
        "candidate_notes": notes,
        **candidate_metadata({"method": "title_content_rule"}),
    }


def build_retrieval(docs: list[Doc]) -> list[dict]:
    specs: list[tuple] = [
        # keyword / title-grounded
        ("RET-001", "企微运营管理办法", "keyword",
         dict(title_any=["企业微信运营管理", "企微运营管理"], content_any=["企业微信", "企微"])),
        ("RET-002", "企业微信平台业务规范", "keyword",
         dict(title_any=["企业微信平台业务", "企业微信运营规范和平台"], content_any=["企业微信"])),
        ("RET-003", "广西公司采购管理办法", "keyword",
         dict(title_any=["采购管理办法"], content_any=["采购"])),
        ("RET-004", "广西公司安全生产管理办法", "keyword",
         dict(title_any=["安全生产管理办法"], content_any=["安全生产"])),
        ("RET-005", "前端业务外包实施细则", "keyword",
         dict(title_any=["前端业务外包"], content_any=["外包"])),
        ("RET-006", "电话外呼实施细则", "keyword",
         dict(title_any=["电话外呼"], content_any=["外呼"])),
        ("RET-007", "客户投诉管理办法", "keyword",
         dict(title_any=["客户投诉"], content_any=["投诉"])),
        ("RET-008", "数据安全管理办法", "keyword",
         dict(title_any=["数据安全管理办法"], content_any=["数据安全"])),
        ("RET-009", "用户个人信息保护管理办法", "keyword",
         dict(title_any=["个人信息保护"], content_any=["个人信息"])),
        ("RET-010", "固定资产管理办法", "keyword",
         dict(title_any=["固定资产管理办法"], content_any=["固定资产"])),
        ("RET-011", "差旅费管理办法", "keyword",
         dict(title_any=["差旅费"], content_any=["差旅"])),
        ("RET-012", "合同管理办法", "keyword",
         dict(title_any=["合同管理办法"], content_any=["合同"])),
        ("RET-013", "星级服务管理办法", "keyword",
         dict(title_any=["星级服务"], content_any=["星级"])),
        ("RET-014", "供应商管理办法", "keyword",
         dict(title_any=["供应商管理办法"], content_any=["供应商"])),
        ("RET-015", "代理商管理规范", "keyword",
         dict(title_any=["代理商管理"], content_any=["代理商"])),
        ("RET-016", "库存物资管理办法", "keyword",
         dict(title_any=["库存物资"], content_any=["库存"])),
        ("RET-017", "全面预算管理办法", "keyword",
         dict(title_any=["全面预算"], content_any=["预算"])),
        ("RET-018", "业务招待管理办法", "keyword",
         dict(title_any=["业务招待"], content_any=["招待"])),
        ("RET-019", "员工奖惩管理办法", "keyword",
         dict(title_any=["员工奖惩"], content_any=["奖惩"])),
        ("RET-020", "往来账款管理办法", "keyword",
         dict(title_any=["往来账款"], content_any=["账款"])),
        ("RET-021", "采购招标投标管理办法", "keyword",
         dict(title_any=["招标投标"], content_any=["招标"])),
        ("RET-022", "采购比选管理办法", "keyword",
         dict(title_any=["采购比选"], content_any=["比选"])),
        ("RET-023", "内容信息安全管理办法", "keyword",
         dict(title_any=["内容信息安全"], content_any=["信息安全"])),
        ("RET-024", "人事档案管理办法", "keyword",
         dict(title_any=["人事档案"], content_any=["档案"])),
        ("RET-025", "本部会议管理办法", "keyword",
         dict(title_any=["会议管理办法"], content_any=["会议"])),
        # synonym / semantic paraphrases of real corpus topics
        ("RET-026", "企业微信", "synonym",
         dict(title_any=["企业微信", "企微"], content_any=["企业微信", "企微"])),
        ("RET-027", "广西公司内控实施细则", "synonym",
         dict(title_any=["内控实施细则"], content_any=["内控"])),
        ("RET-028", "微店一人一码管理", "synonym",
         dict(title_any=["一人一码", "微店"], content_any=["微店", "一人一码"])),
        ("RET-029", "天翼微店运营管理规范", "synonym",
         dict(title_any=["天翼微店", "企业微信-天翼微店"], content_any=["微店", "企业微信"])),
        ("RET-030", "外包费管理办法", "synonym",
         dict(title_any=["外包费"], content_any=["外包"])),
        ("RET-031", "品牌管理办法", "synonym",
         dict(title_any=["品牌管理办法"], content_any=["品牌"])),
        ("RET-032", "原子能力管理办法", "synonym",
         dict(title_any=["原子能力"], content_any=["原子能力"])),
        ("RET-033", "低效固定资产退出", "synonym",
         dict(title_any=["低效固定资产"], content_any=["低效"])),
        ("RET-034", "软件资产管理办法", "synonym",
         dict(title_any=["软件资产"], content_any=["软件"])),
        ("RET-035", "劳动防护用品管理", "synonym",
         dict(title_any=["劳动防护"], content_any=["防护"])),
        ("RET-036", "工作助手运营管理", "synonym",
         dict(title_any=["工作助手"], content_any=["工作助手"])),
        ("RET-037", "采购公示管理办法", "synonym",
         dict(title_any=["采购公示"], content_any=["公示"])),
        ("RET-038", "采购保密信息管理", "synonym",
         dict(title_any=["采购保密"], content_any=["保密"])),
        ("RET-039", "专业岗位动态管理", "synonym",
         dict(title_any=["专业岗位动态"], content_any=["岗位"])),
        ("RET-040", "本部信息公开实施办法", "synonym",
         dict(title_any=["信息公开"], content_any=["信息公开"])),
        # multi-constraint
        ("RET-041", "广西公司 企业微信 运营管理办法", "multi_constraint",
         dict(title_any=["企业微信运营管理"], content_must_any_group=[["广西", "企微", "企业微信"]])),
        ("RET-042", "中国电信 5G 焕新品牌宣传指引", "multi_constraint",
         dict(title_any=["5G", "焕新品牌"], content_any=["5G", "品牌"])),
        ("RET-043", "沙盘系统业务规范", "multi_constraint",
         dict(title_any=["沙盘系统"], content_any=["沙盘"])),
        ("RET-044", "智慧家庭场景化展陈规范", "multi_constraint",
         dict(title_any=["智慧家庭", "展陈"], content_any=["智慧家庭", "展陈"])),
        ("RET-045", "资源效能提升工作方案", "multi_constraint",
         dict(title_any=["资源效能"], content_any=["效能"])),
        ("RET-046", "2026年企微运营质量评分细则", "multi_constraint",
         dict(title_any=["企微运营质量评分", "质量评分细则"], content_any=["评分", "企微"])),
        ("RET-047", "企微集约运营情况汇总", "multi_constraint",
         dict(title_any=["企微集约运营"], content_any=["集约", "企微"])),
        ("RET-048", "一季度企微运营情况", "multi_constraint",
         dict(title_any=["企微运营情况"], content_any=["企微"])),
        ("RET-049", "创智杯销售大赛", "multi_constraint",
         dict(title_any=["创智杯"], content_any=["创智杯", "销售"])),
        ("RET-050", "全渠道认证教材知识点", "multi_constraint",
         dict(title_any=["全渠道认证教材", "知识点归集"], content_any=["全渠道", "认证"])),
        # long / semantic
        ("RET-051", "如何规范企业微信平台的业务与技术要求", "long_query",
         dict(title_any=["企业微信平台业务", "企业微信运营规范和平台"], content_any=["业务规范", "技术规范", "企业微信"])),
        ("RET-052", "广西分公司关于客户投诉处理的制度文件", "long_query",
         dict(title_any=["客户投诉"], content_any=["投诉"])),
        ("RET-053", "关于员工差旅费用报销的管理规定", "long_query",
         dict(title_any=["差旅费"], content_any=["差旅"])),
        ("RET-054", "企业微信粉丝与集约运营相关运营数据表", "long_query",
         dict(title_any=["企微集约运营"], content_any=["粉丝", "集约", "活跃"])),
        ("RET-055", "中国电信广西公司采购与招标相关管理办法", "long_query",
         dict(title_any=["采购管理办法", "招标投标", "采购比选"], content_any=["采购"])),
        ("RET-056", "个人信息与数据安全保护相关制度", "long_query",
         dict(title_any=["个人信息保护", "数据安全管理办法"], content_any=["安全"])),
        ("RET-057", "固定资产及低效资产退出管理", "long_query",
         dict(title_any=["固定资产管理办法", "低效固定资产"], content_any=["资产"])),
        ("RET-058", "业务外包与前端外包实施相关文件", "long_query",
         dict(title_any=["业务外包", "前端业务外包", "外包费"], content_any=["外包"])),
        ("RET-059", "安全生产与劳动防护相关管理要求", "long_query",
         dict(title_any=["安全生产", "劳动防护"], content_any=["安全"])),
        ("RET-060", "社会渠道费用标准相关通知", "long_query",
         dict(title_any=["社会渠道费用"], content_any=["渠道", "费用"])),
        # extra for buffer
        ("RET-061", "内部控制管理办法 财务报告", "keyword",
         dict(title_any=["内部控制", "财务报告"], content_any=["内控", "财务"])),
        ("RET-062", "智慧学习云平台教材提取", "keyword",
         dict(title_any=["智慧学习云平台"], content_any=["教材", "提取"])),
        ("RET-063", "品牌宣传指引 5G", "semantic",
         dict(title_any=["5G", "品牌宣传"], content_any=["品牌"])),
        ("RET-064", "企微数字化赋能工作", "semantic",
         dict(title_any=["企微数字化赋能", "数字化赋能"], content_any=["企微", "数字化"])),
        ("RET-065", "无效粉丝 规则", "semantic",
         dict(content_any=["无效粉丝", "无效粉"], title_any=[])),
    ]

    rows: list[dict] = []
    # distractor for forbidden: test artifact id if present
    test_ids = [d.id for d in docs if "测试工件" in d.title]
    for rid, query, cat, kw in specs:
        exp, acc, _ = find_relevant(docs, **kw)
        if not exp:
            continue
        forbidden = test_ids[:1]
        # also forbid clearly off-topic if we have many
        row = ret_row(
            rid,
            query,
            cat,
            exp,
            acc,
            forbidden,
            difficulty="easy" if cat == "keyword" else "medium",
            notes=f"content-verified title/body match; keywords={kw}",
        )
        if row:
            rows.append(row)
    return rows[:80]  # keep >=60


def build_no_answer(docs: list[Doc]) -> list[dict]:
    # known distractors: docs that share loose tokens but do not answer the query
    def distractors(*terms: str, limit: int = 3) -> list[str]:
        out = []
        for d in docs:
            if any(t in d.title or t in d.content for t in terms):
                out.append(d.id)
            if len(out) >= limit:
                break
        return out

    items = [
        ("NOA-001", "今天公司营收是多少", "requires_current_external_data", "实时财务数据，库内无当日营收"),
        ("NOA-002", "今日股价多少", "requires_current_external_data", "实时股价不在库内"),
        ("NOA-003", "比特币当前价格", "requires_current_external_data", "加密货币实时价格"),
        ("NOA-004", "实时汇率是多少", "requires_current_external_data", "实时汇率"),
        ("NOA-005", "明天天气怎么样", "requires_current_external_data", "天气预报"),
        ("NOA-006", "量子计算最新突破是什么", "not_in_corpus", "库外科技新闻"),
        ("NOA-007", "火星探测任务进度", "not_in_corpus", "库外航天进展"),
        ("NOA-008", "世界杯冠军是谁", "not_in_corpus", "库外体育事实"),
        ("NOA-009", "未公开并购细节", "not_in_corpus", "不存在的机密信息"),
        ("NOA-010", "股票涨跌幅", "requires_current_external_data", "实时行情"),
        ("NOA-011", "本公司 CEO 私人手机号", "not_in_corpus", "隐私信息不在库"),
        ("NOA-012", "2027年广西电信全年利润精确数字", "not_in_corpus", "未来精确财务不在库"),
        ("NOA-013", "OpenAI 最新模型参数量", "not_in_corpus", "外部模型规格"),
        ("NOA-014", "某竞争对手未公开渠道费率表", "not_in_corpus", "外部未公开信息"),
        ("NOA-015", "今天南宁到北京机票最低价", "requires_current_external_data", "实时票价"),
        ("NOA-016", "企微粉丝数现在精确到个位是多少", "requires_current_external_data", "实时精确运营数；库内为历史报表"),
        ("NOA-017", "刚刚上线的内部系统密码", "not_in_corpus", "密钥/口令不在知识库"),
        ("NOA-018", "区块链挖矿收益计算器当前哈希率", "not_in_corpus", "库外技术指标"),
        ("NOA-019", "欧盟最新 AI 法案逐条中文对照（完整版）", "not_in_corpus", "库外法规全文"),
        ("NOA-020", "明日公司股价会涨还是跌", "requires_current_external_data", "预测/实时市场"),
        ("NOA-021", "灯带 60珠/米 的国标全文编号是多少", "insufficient_specific_evidence", "库内可能有珠/米上下文但无国标全文编号保证"),
        ("NOA-022", "张三 2026-07-16 的个人绩效评分", "not_in_corpus", "个人隐私/不存在记录"),
        ("NOA-023", "宇宙中黑洞精确数量", "not_in_corpus", "库外天体物理"),
        ("NOA-024", "当前美元兑人民币中间价", "requires_current_external_data", "实时汇率"),
        ("NOA-025", "广西电信下周开会具体会议室编号", "not_in_corpus", "日程细节不在库"),
        ("NOA-026", "某个不存在的制度：飞天渠道裂变管理办法全文", "not_in_corpus", "虚构制度名"),
        ("NOA-027", "本知识库管理员 root 密码", "not_in_corpus", "系统密钥"),
        ("NOA-028", "昨晚全国停电事故原因", "requires_current_external_data", "实时新闻"),
        ("NOA-029", "苹果公司未发布的 iPhone 规格", "not_in_corpus", "外部未公开产品"),
        ("NOA-030", "任意英文单词 flibbertigibbet 在电信制度中的定义", "not_in_corpus", "无关外来词"),
        ("NOA-031", "今天各市分公司实时加粉排行榜第一名", "requires_current_external_data", "实时排行"),
        ("NOA-032", "量子通信商用合同金额列表（库外）", "not_in_corpus", "库外合同"),
    ]
    rows = []
    for iid, q, reason, notes in items:
        known = []
        if "企微" in q or "粉丝" in q:
            known = distractors("企微", "粉丝")
        elif "采购" in q:
            known = distractors("采购")
        elif "珠" in q:
            known = distractors("珠")
        rows.append(
            {
                "id": iid,
                "query": q,
                "candidate_expected_no_answer": True,
                "candidate_reason": reason,
                "candidate_known_distractor_ids": known,
                "candidate_notes": notes,
                **candidate_metadata({"method": "authored_no_answer_hypothesis"}),
            }
        )
    return rows[:40]


def build_numeric(docs: list[Doc]) -> list[dict]:
    rows: list[dict] = []

    def content_hits(terms: list[str], unit_terms: list[str] | None = None) -> list[str]:
        out = []
        for d in docs:
            if "测试工件" in d.title:
                continue
            if all(t in d.content or t in d.title for t in terms):
                if unit_terms and not any(u in d.content for u in unit_terms):
                    continue
                out.append(d.id)
        return out[:5]

    # meters vs beads confusion
    beads = content_hits(["珠/米"], ["珠/米"])
    meters_docs = []
    for d in docs:
        if "测试工件" in d.title:
            continue
        if re.search(r"\d+\s*米", d.content) and "珠/米" not in d.content:
            meters_docs.append(d.id)
    meters_docs = meters_docs[:5]

    percent_docs = content_hits(["%"], ["%"])
    money_docs = []
    for d in docs:
        if re.search(r"\d+\s*万", d.content) or "万元" in d.content:
            if "测试工件" not in d.title:
                money_docs.append(d.id)
    money_docs = money_docs[:5]

    month_docs = content_hits(["个月"], ["个月"])
    second_docs = []
    for d in docs:
        if re.search(r"\d+\s*秒", d.content) and "测试工件" not in d.title:
            second_docs.append(d.id)
    second_docs = second_docs[:5]

    power_docs = []
    for d in docs:
        if re.search(r"\d+\s*W|功率", d.content) and "测试工件" not in d.title:
            power_docs.append(d.id)
    power_docs = power_docs[:5]

    hu_docs = []
    for d in docs:
        if re.search(r"\d+\s*户|万户", d.content) and "测试工件" not in d.title:
            hu_docs.append(d.id)
    hu_docs = hu_docs[:5]

    candidates = [
        ("NUM-001", "60 米", meters_docs or beads[:1], ["米"], ["珠/米"], beads[:3], False,
         "长度单位米；不得混淆珠/米"),
        ("NUM-002", "60珠/米", beads, ["珠/米"], ["米"], meters_docs[:3], False,
         "密度单位珠/米"),
        ("NUM-003", "60%", percent_docs, ["%"], [], [], False, "百分比"),
        ("NUM-004", "60秒", second_docs, ["秒"], [], [], False, "时间秒"),
        ("NUM-005", "60户", hu_docs, ["户"], [], [], False, "户数"),
        ("NUM-006", "100万元", money_docs, ["万元", "万"], [], [], False, "金额万元"),
        ("NUM-007", "6个月无互动", month_docs, ["个月"], [], [], False, "时长月"),
        ("NUM-008", "6个月试用期", month_docs, ["个月"], [], [], False, "试用期相关月数"),
        ("NUM-009", "12W功率", power_docs, ["W", "功率"], [], [], False, "功率"),
        ("NUM-010", "3.14米", meters_docs, ["米"], ["珠/米"], beads[:2], False, "小数长度"),
        ("NUM-011", "万户规模", hu_docs, ["万户", "户"], [], [], False, "用户规模"),
        ("NUM-012", "日均活跃用户量", hu_docs, [], [], [], False, "活跃用户相关数值文档"),
        ("NUM-013", "平均首次回复时长", content_hits(["回复时长"], ["分钟", "时长"]), ["分钟", "时长"], [], [], False,
         "时长指标"),
        ("NUM-014", "加粉率 55%", percent_docs, ["%"], [], [], False, "加粉率百分比"),
        ("NUM-015", "号码绑定率", percent_docs, ["%"], [], [], False, "绑定率"),
        ("NUM-016", "外包费用标准 万元", money_docs, ["万"], [], [], False, "费用金额"),
        ("NUM-017", "差旅住宿标准", money_docs, [], [], [], False, "金额相关差旅"),
        ("NUM-018", "60珠 灯带", beads, ["珠"], ["米"], meters_docs[:2], False, "珠数勿当长度"),
        ("NUM-019", "米制长度规格 非珠密", meters_docs, ["米"], ["珠/米"], beads[:2], False, "长度非密度"),
        ("NUM-020", "百分比指标 60%", percent_docs, ["%"], [], [], False, "百分比"),
        ("NUM-021", "秒级超时 60秒", second_docs, ["秒"], [], [], False, "秒"),
        ("NUM-022", "功率 12W", power_docs, ["W", "功率"], [], [], False, "功率"),
        ("NUM-023", "金额 100万", money_docs, ["万"], [], [], False, "金额"),
        ("NUM-024", "无对应数值：库外瞬时功耗 9876W", [], ["W"], [], [], True,
         "库内无该精确瞬时功耗，应 no-answer"),
        ("NUM-025", "无对应：今日实时营收 12345.67万元", [], ["万元"], [], [], True,
         "实时营收不在库"),
        ("NUM-026", "珠/米 规格", beads, ["珠/米"], [], meters_docs[:2], False, "珠/米"),
        ("NUM-027", "万户 托管规模", hu_docs, ["万户", "户"], [], [], False, "托管规模"),
        ("NUM-028", "分钟 回复时长", content_hits(["分钟"], ["分钟"]), ["分钟"], [], [], False, "分钟"),
    ]

    for iid, q, exp, units, forb_u, forb_ids, no_ans, notes in candidates:
        if not exp and not no_ans:
            continue
        rows.append(
            {
                "id": iid,
                "query": q,
                "candidate_expected_ids": exp if not no_ans else [],
                "candidate_expected_units": units,
                "candidate_forbidden_units": forb_u,
                "candidate_forbidden_ids": forb_ids,
                "candidate_expected_no_answer": no_ans,
                "candidate_notes": notes,
                **candidate_metadata({"method": "numeric_unit_regex_rule"}),
            }
        )
    return rows


def build_routing() -> list[dict]:
    specs = [
        # graph
        ("ROUTE-001", "文档引用了哪些页面", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-002", "上下游依赖关系", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-003", "被哪些文档引用", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-004", "与企微有什么关联", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-005", "图谱关系路径", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-006", "知识节点之间的边", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-007", "引用链路分析", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-008", "关联文档有哪些", "graph", "graph_traverse", [], "", "graph_result"),
        # structured
        ("ROUTE-009", "列出所有 md 文档", "structured", "execute_query", ["file_type"], "", "structured_result"),
        ("ROUTE-010", "file_type 为 pdf", "structured", "execute_query", ["file_type"], "", "structured_result"),
        ("ROUTE-011", "标签为企微的所有文档", "structured", "execute_query", [], "", "structured_result"),
        ("ROUTE-012", "source_type 为 manual", "structured", "execute_query", [], "", "structured_result"),
        ("ROUTE-013", "统计标签数量", "structured", "execute_query", [], "", "structured_result"),
        ("ROUTE-014", "按 file_type 过滤 docx", "structured", "execute_query", ["file_type"], "", "structured_result"),
        ("ROUTE-015", "列出所有 xlsx 文件", "structured", "execute_query", ["file_type"], "", "structured_result"),
        ("ROUTE-016", "筛选 md 类型知识", "structured", "execute_query", ["file_type"], "", "structured_result"),
        # hybrid / search / ask
        ("ROUTE-017", "广西电信企微未来应该怎么发展", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-018", "总结主要问题", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-019", "对比分析两个方案", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-020", "给出建议和原因", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-021", "综合判断项目风险", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-022", "企业微信运营管理办法要点", "hybrid", "search", [], "", "non_empty"),
        ("ROUTE-023", "采购管理办法关键条款", "hybrid", "search", [], "", "non_empty"),
        ("ROUTE-024", "客户投诉处理流程是什么", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-025", "差旅费报销规定", "hybrid", "search", [], "", "non_empty"),
        ("ROUTE-026", "数据安全管理要求摘要", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-027", "星级服务怎么评定", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-028", "业务外包实施细则说明", "hybrid", "search", [], "", "non_empty"),
        ("ROUTE-029", "安全生产管理办法有哪些要求", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-030", "个人信息保护主要规定", "hybrid", "ask", [], "", "non_empty"),
        # no-answer / current info
        ("ROUTE-031", "今天公司营收是多少", "hybrid", "ask", [], "", "no_answer"),
        ("ROUTE-032", "今日股价多少", "hybrid", "ask", [], "", "no_answer"),
        ("ROUTE-033", "比特币当前价格", "hybrid", "ask", [], "", "no_answer"),
        ("ROUTE-034", "明天天气怎么样", "hybrid", "ask", [], "", "no_answer"),
        ("ROUTE-035", "实时汇率是多少", "hybrid", "ask", [], "", "no_answer"),
        # more graph/structured
        ("ROUTE-036", "页面之间的引用关系", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-037", "依赖图遍历", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-038", "按类型统计文档", "structured", "execute_query", [], "", "structured_result"),
        ("ROUTE-039", "筛选 pdf 文档列表", "structured", "execute_query", ["file_type"], "", "structured_result"),
        ("ROUTE-040", "企微相关文档有哪些关联", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-041", "固定资产管理办法详细解读", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-042", "供应商管理关键要求", "hybrid", "search", [], "", "non_empty"),
        ("ROUTE-043", "合同管理流程", "hybrid", "ask", [], "", "non_empty"),
        ("ROUTE-044", "图谱中节点邻居", "graph", "graph_traverse", [], "", "graph_result"),
        ("ROUTE-045", "列出 file_type=md 的全部条目", "structured", "execute_query", ["file_type"], "", "structured_result"),
    ]
    rows = []
    for iid, q, mode, tool, keys, forb, outcome in specs:
        rows.append(
            {
                "id": iid,
                "query": q,
                "candidate_expected_mode": mode,
                "candidate_expected_tool": tool,
                "candidate_required_argument_keys": keys,
                "candidate_forbidden_tool": forb,
                "candidate_expected_task_outcome": outcome,
                **candidate_metadata({"method": "authored_routing_hypothesis"}),
            }
        )
    return rows


def build_answer_citations(docs: list[Doc]) -> list[dict]:
    # Pair factual questions with supporting docs verified by title/content.
    pairs = [
        ("ANS-001", "中国电信广西公司企业微信运营管理办法的主题是什么？",
         ["企业微信运营管理"], ["企业微信", "运营"],
         "制度主题为企业微信运营管理"),
        ("ANS-002", "广西公司客户投诉管理办法规范什么？",
         ["客户投诉"], ["投诉"],
         "规范客户投诉管理"),
        ("ANS-003", "差旅费管理办法适用于什么费用？",
         ["差旅费"], ["差旅"],
         "适用于差旅相关费用管理"),
        ("ANS-004", "数据安全管理办法主要保护什么？",
         ["数据安全管理办法"], ["数据安全"],
         "保护数据安全"),
        ("ANS-005", "个人信息保护管理办法约束什么信息？",
         ["个人信息保护"], ["个人信息"],
         "用户个人信息保护"),
        ("ANS-006", "星级服务管理办法的目标是什么？",
         ["星级服务"], ["星级"],
         "提升星级客户服务"),
        ("ANS-007", "采购管理办法规范什么行为？",
         ["采购管理办法"], ["采购"],
         "规范采购行为"),
        ("ANS-008", "安全生产管理办法关注什么领域？",
         ["安全生产管理办法"], ["安全生产"],
         "安全生产管理"),
        ("ANS-009", "固定资产管理办法管理哪类资产？",
         ["固定资产管理办法"], ["固定资产"],
         "固定资产"),
        ("ANS-010", "合同管理办法规范什么？",
         ["合同管理办法"], ["合同"],
         "合同管理"),
        ("ANS-011", "供应商管理办法的对象是谁？",
         ["供应商管理办法"], ["供应商"],
         "供应商"),
        ("ANS-012", "代理商管理规范针对什么主体？",
         ["代理商管理"], ["代理商"],
         "代理商"),
        ("ANS-013", "电话外呼实施细则规范什么活动？",
         ["电话外呼"], ["外呼"],
         "电话外呼"),
        ("ANS-014", "前端业务外包实施细则涉及什么？",
         ["前端业务外包"], ["外包"],
         "前端业务外包"),
        ("ANS-015", "业务招待管理办法管理什么支出？",
         ["业务招待"], ["招待"],
         "业务招待"),
        ("ANS-016", "员工奖惩管理办法规范什么？",
         ["员工奖惩"], ["奖惩"],
         "员工奖惩"),
        ("ANS-017", "全面预算管理办法关注什么？",
         ["全面预算"], ["预算"],
         "全面预算"),
        ("ANS-018", "人事档案管理办法管理什么材料？",
         ["人事档案"], ["档案"],
         "人事档案"),
        ("ANS-019", "库存物资管理办法管理什么？",
         ["库存物资"], ["库存"],
         "库存物资"),
        ("ANS-020", "内容信息安全管理办法保护什么？",
         ["内容信息安全"], ["信息安全"],
         "内容信息安全"),
        ("ANS-021", "企业微信平台业务规范的制定主体相关文件讲什么？",
         ["企业微信平台业务", "企业微信运营规范和平台"], ["企业微信"],
         "企业微信平台业务/技术规范"),
        ("ANS-022", "天翼微店运营管理规范与什么渠道相关？",
         ["天翼微店", "企业微信-天翼微店"], ["微店", "企业微信"],
         "企业微信/天翼微店运营"),
        ("ANS-023", "5G 焕新品牌宣传指引讲什么？",
         ["5G", "焕新品牌"], ["5G", "品牌"],
         "5G 焕新品牌宣传"),
        ("ANS-024", "低效固定资产退出管理办法规范什么？",
         ["低效固定资产"], ["低效", "资产"],
         "低效固定资产退出"),
        ("ANS-025", "软件资产管理办法管理什么资产？",
         ["软件资产"], ["软件"],
         "软件资产"),
        ("ANS-026", "劳动防护用品管理办法规范什么？",
         ["劳动防护"], ["防护"],
         "劳动防护用品"),
        ("ANS-027", "往来账款管理办法管理什么账款？",
         ["往来账款"], ["账款"],
         "往来账款"),
        ("ANS-028", "采购比选管理办法规范什么采购方式？",
         ["采购比选"], ["比选"],
         "采购比选"),
    ]
    rows = []
    for iid, question, title_any, content_any, fact in pairs:
        exp, acc, _ = find_relevant(
            docs,
            title_any=title_any,
            content_any=content_any,
            max_expected=3,
        )
        support = exp or acc
        if not support:
            continue
        rows.append(
            {
                "id": iid,
                "question": question,
                "candidate_expected_answer_facts": [
                    {
                        "fact_id": "F1",
                        "statement": fact,
                        "supporting_knowledge_ids": support[:3],
                        "supporting_block_ids": [],
                        "supporting_quotes": [],
                    }
                ],
                "candidate_forbidden_claims": ["不在文档中的精确实时数据", "编造的条款编号"],
                "candidate_minimum_sources": 1,
                "candidate_notes": "title/content rules found these candidate documents; facts require body/block review",
                **candidate_metadata({"method": "title_content_rule"}),
            }
        )
    return rows


def main() -> None:
    docs = load_docs()
    retrieval = build_retrieval(docs)
    no_answer = build_no_answer(docs)
    numeric = build_numeric(docs)
    routing = build_routing()
    answers = build_answer_citations(docs)

    write_jsonl(OUT_DIR / "production_pilot_retrieval.candidates.jsonl", retrieval)
    write_jsonl(OUT_DIR / "production_pilot_no_answer.candidates.jsonl", no_answer)
    write_jsonl(OUT_DIR / "production_pilot_numeric_units.candidates.jsonl", numeric)
    write_jsonl(OUT_DIR / "production_pilot_routing.candidates.jsonl", routing)
    write_jsonl(OUT_DIR / "production_pilot_answer_citations.candidates.jsonl", answers)

    summary = {
        "corpus_snapshot_sha": CORPUS_SNAPSHOT,
        "formal_db_sha256_prefix": SHA,
        "counts": {
            "retrieval": len(retrieval),
            "no_answer": len(no_answer),
            "numeric_units": len(numeric),
            "routing": len(routing),
            "answer_citations": len(answers),
        },
        "pad_excluded": True,
        "hit_or_empty_excluded": True,
        "empty_candidate_expected_ids_in_retrieval": sum(
            1 for r in retrieval if not r.get("candidate_expected_ids")
        ),
        "annotation_method": "rule-assisted candidates only; no human decisions or frozen GT",
    }
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "candidate-generation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    assert summary["counts"]["retrieval"] >= 60, summary
    assert summary["counts"]["no_answer"] >= 30, summary
    assert summary["counts"]["numeric_units"] >= 25, summary
    assert summary["counts"]["routing"] >= 40, summary
    assert summary["counts"]["answer_citations"] >= 25, summary
    assert summary["empty_candidate_expected_ids_in_retrieval"] == 0, summary


if __name__ == "__main__":
    main()
