"""Wiki 静态站点生成器"""
import json
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

from src.services.db import Database
from src.services.wiki_seo import SEOMetadataGenerator, WikiSEOMetadata
from src.services.wiki_compiler import parse_tags
from src.utils.config import Config

logger = logging.getLogger(__name__)


class WikiSiteGenerator:
    """Wiki 静态站点生成器"""

    def __init__(self, output_dir: str = "wiki_site"):
        self.output_dir = Path(output_dir)
        self.site_title = Config.get("wiki.site.site_title", "知识库 Wiki")
        self.site_description = Config.get("wiki.site.site_description", "")
        self.base_url = Config.get("wiki.site.base_url", "")
        self.landing_sections = Config.get("wiki.site.landing_page_sections",
                                            ["project_intro", "recent_articles", "categories", "stats"])

    def generate_static_site(self, output_dir: str | None = None) -> Path:
        """生成静态站点"""
        if output_dir:
            self.output_dir = Path(output_dir)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Generating static site to {self.output_dir}")

        # 1. 获取所有已发布页面
        pages = Database.list_wiki_pages(status="published", limit=10000)

        # 2. 渲染每个页面
        for page in pages:
            self._render_page(page)

        # 3. 生成 Landing Page
        self._render_landing_page(pages)

        # 4. 生成 sitemap.xml
        self._generate_sitemap(pages)

        # 5. 生成 search.json
        self._generate_search_index(pages)

        logger.info(f"Static site generated: {len(pages)} pages")
        return self.output_dir

    def _render_page(self, page: dict) -> Path:
        """渲染单个页面为 HTML"""
        from src.services.wiki_site_renderer import render_wiki_page

        html = render_wiki_page(page, {
            "site_title": self.site_title,
            "site_description": self.site_description,
            "base_url": self.base_url,
        })

        output_path = self.output_dir / "pages" / f"{page['id']}.html"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

        return output_path

    def _render_landing_page(self, pages: list[dict]) -> Path:
        """渲染 Landing Page"""
        from src.services.wiki_site_renderer import render_landing_page

        stats = {
            "total_pages": len(pages),
            "total_knowledge": Database.count_knowledge(),
            "last_updated": max((p.get("updated_at", "") for p in pages), default=""),
        }

        # 获取最近更新的页面
        recent_pages = sorted(pages, key=lambda p: p.get("updated_at", ""), reverse=True)[:10]

        # 按标签分类
        categories = {}
        for page in pages:
            tags = parse_tags(page.get("tags", "[]"))
            for tag in tags:
                categories.setdefault(tag, []).append(page)

        html = render_landing_page({
            "site_title": self.site_title,
            "site_description": self.site_description,
            "sections": self.landing_sections,
            "recent_pages": recent_pages,
            "categories": categories,
            "stats": stats,
        }, self.base_url)

        output_path = self.output_dir / "index.html"
        output_path.write_text(html, encoding="utf-8")
        return output_path

    def _generate_sitemap(self, pages: list[dict]) -> Path:
        """生成 sitemap.xml"""
        from xml.sax.saxutils import escape as xml_escape

        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

        for page in pages:
            url = f"{self.base_url}/pages/{page['id']}" if self.base_url else f"pages/{page['id']}.html"
            lastmod = page.get("updated_at", "")
            priority = "0.8"

            lines.append("  <url>")
            lines.append(f"    <loc>{xml_escape(url)}</loc>")
            if lastmod:
                lines.append(f"    <lastmod>{xml_escape(lastmod)}</lastmod>")
            lines.append(f"    <priority>{priority}</priority>")
            lines.append("  </url>")

        lines.append("</urlset>")

        output_path = self.output_dir / "sitemap.xml"
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    def _generate_search_index(self, pages: list[dict]) -> Path:
        """生成客户端搜索索引 JSON"""
        search_index = []

        for page in pages:
            search_index.append({
                "id": page["id"],
                "title": page["title"],
                "summary": page.get("concept_summary", ""),
                "content": page.get("content", "")[:500],  # 截取前500字符
                "tags": parse_tags(page.get("tags", "[]")),
                "url": f"pages/{page['id']}.html",
            })

        output_path = self.output_dir / "search.json"
        output_path.write_text(json.dumps(search_index, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path


class WikiSiteRenderer:
    """FastAPI 实时渲染器（可选，替代静态导出）"""

    def __init__(self):
        self.site_title = Config.get("wiki.site.site_title", "知识库 Wiki")
        self.site_description = Config.get("wiki.site.site_description", "")
        self.base_url = Config.get("wiki.site.base_url", "")

    def render_page(self, page_id: str) -> Optional[str]:
        """实时渲染单个页面"""
        page = Database.get_wiki_page(page_id)
        if not page or page.get("status") != "published":
            return None

        from src.services.wiki_site_renderer import render_wiki_page
        return render_wiki_page(page, {
            "site_title": self.site_title,
            "site_description": self.site_description,
            "base_url": self.base_url,
        })

    def render_landing(self) -> str:
        """实时渲染首页"""
        pages = Database.list_wiki_pages(status="published", limit=100)
        stats = {
            "total_pages": len(pages),
            "total_knowledge": Database.count_knowledge(),
        }
        recent = sorted(pages, key=lambda p: p.get("updated_at", ""), reverse=True)[:10]

        from src.services.wiki_site_renderer import render_landing_page
        return render_landing_page({
            "site_title": self.site_title,
            "site_description": self.site_description,
            "sections": Config.get("wiki.site.landing_page_sections",
                                   ["project_intro", "recent_articles", "categories", "stats"]),
            "recent_pages": recent,
            "stats": stats,
        }, self.base_url)

    def render_search(self, query: str) -> list[dict]:
        """搜索页面"""
        if not query:
            return []

        # 简单标题搜索
        pages = Database.list_wiki_pages(status="published", search=query, limit=20)
        return [{"id": p["id"], "title": p["title"], "summary": p.get("concept_summary", "")} for p in pages]