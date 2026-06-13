"""Wiki SEO 元数据模型"""
import json
from dataclasses import asdict, dataclass, field

from src.services.wiki_compiler import parse_tags


@dataclass
class WikiSEOMetadata:
    """Wiki 页面 SEO 元数据"""
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    og_title: str = ""
    og_description: str = ""
    canonical_url: str = ""
    structured_data_type: str = "Article"  # Article, TechArticle, FAQPage, CollectionPage

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> "WikiSEOMetadata":
        """从 JSON 字符串反序列化"""
        try:
            data = json.loads(json_str)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            return cls()

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)


class SEOMetadataGenerator:
    """SEO 元数据生成器"""

    @staticmethod
    def generate_from_page(page: dict, base_url: str = "") -> WikiSEOMetadata:
        """从 Wiki 页面生成 SEO 元数据"""
        title = page.get("title", "")
        summary = page.get("concept_summary", "") or ""
        content = page.get("content", "") or ""

        # 提取关键词（从 tags 和 summary 抽取）
        tags = parse_tags(page.get("tags", "[]"))

        # 生成 description
        description = summary
        if not description and content:
            description = content[:200].strip() + "..." if len(content) > 200 else content

        # Open Graph
        og_title = title
        og_description = description[:200] if description else ""

        # Canonical URL
        canonical_url = f"{base_url}/pages/{page['id']}" if base_url else ""

        return WikiSEOMetadata(
            description=description,
            keywords=tags if isinstance(tags, list) else [],
            og_title=og_title,
            og_description=og_description,
            canonical_url=canonical_url,
            structured_data_type="TechArticle",
        )

    @staticmethod
    def generate_meta_tags(seo: WikiSEOMetadata) -> str:
        """生成 HTML meta 标签"""
        import html as _html
        tags = []

        if seo.description:
            tags.append(f'<meta name="description" content="{_html.escape(seo.description, quote=True)}">')

        if seo.keywords:
            escaped_kw = _html.escape(", ".join(seo.keywords), quote=True)
            tags.append(f'<meta name="keywords" content="{escaped_kw}">')

        if seo.og_title:
            tags.append(f'<meta property="og:title" content="{_html.escape(seo.og_title, quote=True)}">')

        if seo.og_description:
            tags.append(f'<meta property="og:description" content="{_html.escape(seo.og_description, quote=True)}">')

        if seo.canonical_url:
            tags.append(f'<link rel="canonical" href="{_html.escape(seo.canonical_url, quote=True)}">')

        return "\n".join(tags)

    @staticmethod
    def generate_structured_data(page: dict, seo: WikiSEOMetadata) -> str:
        """生成 JSON-LD 结构化数据"""
        import html

        data = {
            "@context": "https://schema.org",
            "@type": seo.structured_data_type,
            "headline": page.get("title", ""),
            "description": seo.description,
            "datePublished": page.get("created_at", ""),
            "dateModified": page.get("updated_at", ""),
            "keywords": ", ".join(seo.keywords) if seo.keywords else "",
        }

        # 转义 HTML 实体
        for k, v in data.items():
            if isinstance(v, str):
                data[k] = html.escape(v)

        return json.dumps(data, ensure_ascii=False, indent=2)
