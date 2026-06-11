"""预设分类体系 — 电信企业知识管理分类标准

面向中国电信等大型通信企业的知识管理场景，覆盖战略、市场、网络、
客服、产品、财务、人力、IT、采购、法律等核心业务领域。
分类为两级结构，LLM 从中选择归入，不可自创类别。
"""

CLASSIFICATION_SCHEMA = [
    {
        "code": "A",
        "name": "战略与管理",
        "description": "企业战略规划、经营分析、绩效考核、管理制度",
        "subcategories": [
            {"code": "A1", "name": "企业战略规划"},
            {"code": "A2", "name": "经营分析"},
            {"code": "A3", "name": "绩效考核"},
            {"code": "A4", "name": "管理制度规范"},
        ],
    },
    {
        "code": "B",
        "name": "市场营销",
        "description": "市场研究、品牌管理、营销策划、广告宣传",
        "subcategories": [
            {"code": "B1", "name": "市场研究与分析"},
            {"code": "B2", "name": "品牌管理"},
            {"code": "B3", "name": "营销策划与执行"},
            {"code": "B4", "name": "广告与传播"},
        ],
    },
    {
        "code": "C",
        "name": "渠道与销售",
        "description": "实体渠道、电子渠道、政企销售、渠道运营管理",
        "subcategories": [
            {"code": "C1", "name": "实体渠道管理"},
            {"code": "C2", "name": "电子渠道运营"},
            {"code": "C3", "name": "政企销售"},
            {"code": "C4", "name": "渠道合作与赋能"},
        ],
    },
    {
        "code": "D",
        "name": "客户服务",
        "description": "服务规范、投诉管理、客户关系管理、服务质量",
        "subcategories": [
            {"code": "D1", "name": "服务标准与规范"},
            {"code": "D2", "name": "投诉与工单管理"},
            {"code": "D3", "name": "客户关系管理"},
            {"code": "D4", "name": "服务质量与满意度"},
        ],
    },
    {
        "code": "E",
        "name": "网络与运维",
        "description": "网络建设、网络运维、网络优化、应急通信保障",
        "subcategories": [
            {"code": "E1", "name": "网络建设与规划"},
            {"code": "E2", "name": "网络运维管理"},
            {"code": "E3", "name": "网络优化与质量"},
            {"code": "E4", "name": "应急通信保障"},
        ],
    },
    {
        "code": "F",
        "name": "产品与业务",
        "description": "移动业务、宽带业务、政企产品、新兴业务、终端设备",
        "subcategories": [
            {"code": "F1", "name": "移动通信业务"},
            {"code": "F2", "name": "宽带与固网业务"},
            {"code": "F3", "name": "政企产品与方案"},
            {"code": "F4", "name": "新兴业务(云/大数据/AI)"},
            {"code": "F5", "name": "终端与智能设备"},
        ],
    },
    {
        "code": "G",
        "name": "采购与供应链",
        "description": "采购管理、供应商管理、合同管理、仓储物流",
        "subcategories": [
            {"code": "G1", "name": "采购流程与管理"},
            {"code": "G2", "name": "供应商管理"},
            {"code": "G3", "name": "合同与招投标"},
            {"code": "G4", "name": "仓储与物流"},
        ],
    },
    {
        "code": "H",
        "name": "财务与审计",
        "description": "财务管理、预算管理、内部审计、资产管理",
        "subcategories": [
            {"code": "H1", "name": "财务核算与管理"},
            {"code": "H2", "name": "预算与成本管控"},
            {"code": "H3", "name": "内部审计与监督"},
            {"code": "H4", "name": "资产管理"},
        ],
    },
    {
        "code": "I",
        "name": "人力资源",
        "description": "招聘配置、培训发展、薪酬福利、组织架构",
        "subcategories": [
            {"code": "I1", "name": "招聘与人员配置"},
            {"code": "I2", "name": "培训与人才发展"},
            {"code": "I3", "name": "薪酬与绩效"},
            {"code": "I4", "name": "组织架构与编制"},
        ],
    },
    {
        "code": "J",
        "name": "信息化与IT",
        "description": "IT系统建设、信息安全、数据管理、数字化转型",
        "subcategories": [
            {"code": "J1", "name": "IT系统规划与建设"},
            {"code": "J2", "name": "信息安全管理"},
            {"code": "J3", "name": "数据治理与管理"},
            {"code": "J4", "name": "数字化转型"},
        ],
    },
    {
        "code": "K",
        "name": "法律与合规",
        "description": "法律事务、合规管理、知识产权、行业监管政策",
        "subcategories": [
            {"code": "K1", "name": "法律事务"},
            {"code": "K2", "name": "合规管理"},
            {"code": "K3", "name": "知识产权"},
            {"code": "K4", "name": "行业监管与政策"},
        ],
    },
    {
        "code": "L",
        "name": "党建与企业文化",
        "description": "党建工作、企业文化、社会责任、工会事务",
        "subcategories": [
            {"code": "L1", "name": "党建工作"},
            {"code": "L2", "name": "企业文化与宣传"},
            {"code": "L3", "name": "社会责任"},
            {"code": "L4", "name": "工会与员工关怀"},
        ],
    },
    {
        "code": "M",
        "name": "安全与风控",
        "description": "安全生产、风险管理、内控管理、信息安全",
        "subcategories": [
            {"code": "M1", "name": "安全生产管理"},
            {"code": "M2", "name": "全面风险管理"},
            {"code": "M3", "name": "内部控制"},
        ],
    },
    {
        "code": "N",
        "name": "项目管理",
        "description": "项目立项、项目执行与管控、项目验收评估",
        "subcategories": [
            {"code": "N1", "name": "项目立项与评审"},
            {"code": "N2", "name": "项目执行与管控"},
            {"code": "N3", "name": "项目验收与评估"},
        ],
    },
    {
        "code": "O",
        "name": "互联网知识",
        "description": "从互联网获取的网页文章、技术博客、新闻资讯、在线文档",
        "subcategories": [
            {"code": "O1", "name": "技术文章与博客"},
            {"code": "O2", "name": "新闻与资讯"},
            {"code": "O3", "name": "在线文档与教程"},
            {"code": "O4", "name": "其他网络资源"},
        ],
    },
]

# 兜底未分类类别
UNCATEGORIZED = {"code": "Z", "name": "未分类", "description": "无法归入上述类别的知识条目"}


def get_schema_prompt() -> str:
    """将分类体系格式化为 LLM prompt 中的参考文本"""
    lines = []
    for cat in CLASSIFICATION_SCHEMA:
        lines.append(f"- {cat['code']} {cat['name']}：{cat['description']}")
        for sub in cat.get("subcategories", []):
            lines.append(f"  - {sub['code']} {sub['name']}")
    lines.append(f"- {UNCATEGORIZED['code']} {UNCATEGORIZED['name']}：{UNCATEGORIZED['description']}")
    return "\n".join(lines)


def get_all_codes(include_db=False) -> set[str]:
    """返回所有有效的分类代码（含大类和子类）。include_db 时合并数据库中的动态分类。"""
    codes = set()
    for cat in CLASSIFICATION_SCHEMA:
        codes.add(cat["code"])
        for sub in cat.get("subcategories", []):
            codes.add(sub["code"])
    codes.add(UNCATEGORIZED["code"])
    if include_db:
        try:
            from src.services.db import Database
            for cat in Database.get_all_categories():
                name = cat.get("name", "")
                # DB 分类名格式为 "CODE 名称"，取 code 部分
                parts = name.split(" ", 1)
                if parts:
                    codes.add(parts[0])
        except Exception:
            pass
    return codes
