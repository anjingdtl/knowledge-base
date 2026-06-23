"""标签自动推理 — 从标题/路径/TF-IDF/词表/LLM 推断知识条目标签"""
import json
import logging
import re
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

# ---- 常见业务领域关键词 → 标签映射 ----
_TITLE_TAG_PATTERNS = [
    # (正则模式, 推断标签)
    (r"企微|企业微信|微信", ["企微"]),
    (r"外包|外包管理", ["外包管理"]),
    (r"劳动竞赛|创智杯|竞赛", ["劳动竞赛"]),
    (r"渠道|全渠道|渠道运营", ["渠道运营"]),
    (r"安全|信息安全|网络安全|网信安", ["安全"]),
    (r"品牌|品牌管理", ["品牌管理"]),
    (r"广告|宣传|广告宣传", ["广告宣传"]),
    (r"翼支付", ["翼支付"]),
    (r"智能表格|表格", ["智能表格"]),
    (r"认证|认证教材|教材", ["认证教材"]),
    (r"抖音|本地生活", ["抖音本地生活"]),
    (r"内控|内控细则|内控管理", ["内控"]),
    (r"采购|采购管理|单一来源", ["采购管理"]),
    (r"权益|权益业务", ["权益业务"]),
    (r"AI|数字化|人工智能", ["AI数字化"]),
    (r"运营报告|运营质量|运营情况", ["运营报告"]),
    (r"能力建设|建设项目", ["能力建设"]),
    (r"问需|产品问需", ["产品问需"]),
    (r"评价|综合评价", ["综合评价"]),
    (r"先审后发", ["企微", "安全"]),
    (r"加粉|粉丝|企微粉丝", ["企微", "渠道运营"]),
]

# 路径 → 标签映射
_PATH_TAG_PATTERNS = [
    (r"渠道运营[/\\]", ["渠道运营"]),
    (r"企微[/\\]|企业微信[/\\]", ["企微"]),
    (r"安全[/\\]|网信安[/\\]", ["安全"]),
    (r"竞赛[/\\]|创智杯[/\\]", ["劳动竞赛"]),
    (r"外包[/\\]", ["外包管理"]),
    (r"培训[/\\]|教材[/\\]", ["认证教材"]),
]


def infer_tags_from_title(title: str) -> list[str]:
    """从标题正则提取标签关键词"""
    if not title:
        return []
    tags = []
    seen = set()
    for pattern, label_tags in _TITLE_TAG_PATTERNS:
        if re.search(pattern, title):
            for t in label_tags:
                if t not in seen:
                    tags.append(t)
                    seen.add(t)
    return tags


def infer_tags_from_path(source_path: str) -> list[str]:
    """从文档路径推断分类标签"""
    if not source_path:
        return []
    tags = []
    seen = set()
    for pattern, label_tags in _PATH_TAG_PATTERNS:
        if re.search(pattern, source_path):
            for t in label_tags:
                if t not in seen:
                    tags.append(t)
                    seen.add(t)
    return tags


def infer_tags_from_tfidf(content: str, top_k: int = 3) -> list[str]:
    """基于简单词频提取高频关键词作为候选标签（轻量版TF-IDF）"""
    if not content or len(content) < 20:
        return []
    try:
        import jieba
        words = jieba.lcut(content)
    except ImportError:
        return []
    # 过滤停用词和短词
    stopwords = {"的", "了", "是", "在", "和", "与", "或", "等", "中", "为",
                 "对", "按", "将", "其", "该", "各", "以", "及", "到", "从",
                 "由", "被", "把", "让", "给", "用", "向", "于", "上", "下",
                 "不", "有", "无", "可", "要", "会", "能", "这", "那", "一"}
    valid_words = [w for w in words if len(w) >= 2 and w not in stopwords]
    if not valid_words:
        return []
    counter = Counter(valid_words)
    return [w for w, _ in counter.most_common(top_k)]


def infer_tags_from_existing_vocab(text: str, vocab: list[str]) -> list[str]:
    """与已有标签词表匹配"""
    if not text or not vocab:
        return []
    matched = []
    for tag in vocab:
        if tag in text:
            matched.append(tag)
    return matched


def infer_tags_by_llm(title: str, content: str, existing_vocab: list[str]) -> list[dict]:
    """LLM 批量推断标签（异步，结果需人工确认）

    Returns:
        list of {"tag": str, "confidence": float, "inferred": True}
    """
    if not title and not content:
        return []
    try:
        from src.services.llm import LLMService
        llm = LLMService()
    except Exception as e:
        logger.warning(f"LLM service unavailable for tag inference: {e}")
        return []

    vocab_hint = ", ".join(existing_vocab[:30]) if existing_vocab else "无"
    sample_content = (content or "")[:800]

    prompt = f"""你是一个文档分类专家。请根据以下文档的标题和内容摘录，推断最合适的标签。

已有标签词表：{vocab_hint}

文档标题：{title}
内容摘录：{sample_content}

请返回JSON数组，每个元素包含 tag（标签名）和 confidence（0-1的置信度）。
只返回JSON，不要其他内容。标签应尽量从已有标签词表中选择，最多5个标签。
示例：[{{"tag": "企微", "confidence": 0.9}}]"""

    try:
        response = llm.chat(prompt, max_tokens=300, temperature=0.1)
        # 解析 JSON
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        results = json.loads(text)
        return [
            {"tag": r["tag"], "confidence": float(r.get("confidence", 0.5)), "inferred": True}
            for r in results if isinstance(r, dict) and "tag" in r
        ]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(f"Failed to parse LLM tag inference response: {e}")
        return []
    except Exception as e:
        logger.error(f"LLM tag inference failed: {e}")
        return []


def infer_tags(item: dict, vocab: list[str] | None = None, use_llm: bool = False) -> list[dict]:
    """统一标签推理入口

    Args:
        item: 知识条目dict，需含 title, source_path, content 字段
        vocab: 已有标签词表（可选）
        use_llm: 是否启用LLM推理（默认关闭）

    Returns:
        list of {"tag": str, "source": str, "confidence": float, "inferred": bool}
    """
    if vocab is None:
        vocab = []

    results = []
    seen_tags = set()

    def _add(tag: str, source: str, confidence: float, inferred: bool):
        if tag and tag not in seen_tags:
            results.append({
                "tag": tag,
                "source": source,
                "confidence": confidence,
                "inferred": inferred,
            })
            seen_tags.add(tag)

    # Level 1: 规则推理
    for t in infer_tags_from_title(item.get("title", "")):
        _add(t, "title_pattern", 0.9, True)
    for t in infer_tags_from_path(item.get("source_path", "")):
        _add(t, "path_pattern", 0.85, True)
    for t in infer_tags_from_existing_vocab(item.get("title", "") + " " + item.get("content", "")[:200], vocab):
        _add(t, "vocab_match", 0.75, True)
    for t in infer_tags_from_tfidf(item.get("content", ""), top_k=3):
        _add(t, "tfidf", 0.6, True)

    # Level 2: LLM推理（仅在规则推理结果不足时）
    if use_llm and len(results) < 2:
        llm_results = infer_tags_by_llm(
            item.get("title", ""),
            item.get("content", ""),
            vocab,
        )
        for r in llm_results:
            _add(r["tag"], "llm", r.get("confidence", 0.5), True)

    return results
