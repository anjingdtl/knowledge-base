"""Wiki 站点 Jinja2 渲染器"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 模板目录
TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "wiki"

# 尝试导入 Jinja2，如果不可用则使用简单渲染
try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from markdown import markdown

    JINJA_AVAILABLE = True
except ImportError:
    JINJA_AVAILABLE = False
    logger.warning("Jinja2 or markdown not available, using simple renderer")


def render_wiki_page(page: dict, site_config: dict) -> str:
    """渲染 Wiki 页面为 HTML"""
    if JINJA_AVAILABLE:
        return _render_jinja_page(page, site_config)
    return _render_simple_page(page, site_config)


def render_landing_page(data: dict, base_url: str = "") -> str:
    """渲染 Landing Page"""
    if JINJA_AVAILABLE:
        return _render_jinja_landing(data, base_url)
    return _render_simple_landing(data)


def _render_jinja_page(page: dict, site_config: dict) -> str:
    """使用 Jinja2 渲染页面"""
    try:
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        template = env.get_template("page.html")

        # 转换 Markdown 为 HTML
        content_html = markdown(page.get("content", ""), extensions=["extra", "codehilite"])

        # 生成 SEO 元数据
        from src.services.wiki_seo import SEOMetadataGenerator
        seo = SEOMetadataGenerator.generate_from_page(page, site_config.get("base_url", ""))
        meta_tags = SEOMetadataGenerator.generate_meta_tags(seo)
        structured_data = SEOMetadataGenerator.generate_structured_data(page, seo)

        return template.render(
            page=page,
            content_html=content_html,
            site_title=site_config.get("site_title", "Wiki"),
            site_description=site_config.get("site_description", ""),
            meta_tags=meta_tags,
            structured_data=structured_data,
            base_url=site_config.get("base_url", ""),
        )
    except Exception as e:
        logger.error(f"Jinja2 render failed: {e}")
        return _render_simple_page(page, site_config)


def _render_jinja_landing(data: dict, base_url: str) -> str:
    """使用 Jinja2 渲染首页"""
    try:
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        template = env.get_template("landing.html")

        return template.render(
            site_title=data.get("site_title", "Wiki"),
            site_description=data.get("site_description", ""),
            sections=data.get("sections", []),
            recent_pages=data.get("recent_pages", []),
            categories=data.get("categories", {}),
            stats=data.get("stats", {}),
            base_url=base_url,
        )
    except Exception as e:
        logger.error(f"Jinja2 landing render failed: {e}")
        return _render_simple_landing(data)


def _render_simple_page(page: dict, site_config: dict) -> str:
    """简单渲染（无 Jinja2）"""
    import html as _html

    title = _html.escape(page.get('title', ''))
    summary = _html.escape(page.get('concept_summary', ''))
    content = _html.escape(page.get('content', ''))
    # 简单 Markdown 转换（在已转义的安全文本上操作）
    import re as _re
    # 标题：逐行匹配
    lines = content.split('\n')
    converted_lines = []
    for line in lines:
        if line.startswith('### '):
            converted_lines.append(f'<h3>{line[4:]}</h3>')
        elif line.startswith('## '):
            converted_lines.append(f'<h2>{line[3:]}</h2>')
        elif line.startswith('# '):
            converted_lines.append(f'<h1>{line[2:]}</h1>')
        else:
            converted_lines.append(line)
    content = '\n'.join(converted_lines)
    # 粗体和斜体：交替配对
    content = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
    content = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', content)
    content = content.replace('\n\n', '</p><p>')

    site_title = _html.escape(site_config.get('site_title', 'Wiki'))
    back_url = _html.escape(site_config.get('base_url', ''))

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title} - {site_title}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
        h1, h2, h3 {{ color: #333; }}
        a {{ color: #0066cc; }}
        code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }}
        pre {{ background: #f4f4f4; padding: 15px; overflow-x: auto; }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <p>{summary}</p>
    <div>{content}</div>
    <hr>
    <footer>
        <a href="{back_url}/index.html">返回首页</a>
    </footer>
</body>
</html>"""


def _render_simple_landing(data: dict) -> str:
    """简单渲染首页"""
    site_title = data.get("site_title", "Wiki")
    stats = data.get("stats", {})
    recent = data.get("recent_pages", [])

    recent_html = ""
    for p in recent:
        recent_html += f'<li><a href="pages/{p["id"]}.html">{p["title"]}</a></li>'

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{site_title}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 800px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #333; }}
        .stats {{ background: #f5f5f5; padding: 15px; border-radius: 8px; margin: 20px 0; }}
        ul {{ list-style: none; padding: 0; }}
        li {{ margin: 10px 0; }}
        a {{ color: #0066cc; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <h1>{site_title}</h1>
    <p>{data.get('site_description', '')}</p>

    <div class="stats">
        <h3>统计信息</h3>
        <p>Wiki 页面: {stats.get('total_pages', 0)}</p>
        <p>知识条目: {stats.get('total_knowledge', 0)}</p>
    </div>

    <h2>最近更新</h2>
    <ul>
        {recent_html}
    </ul>
</body>
</html>"""