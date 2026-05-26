"""生成 ShineHeKnowledge 用户说明文档 (DOCX)

用法: python scripts/build_docs.py
输出: docs/ShineHeKnowledge_UserManual_vX.X.X.docx
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.version import APP_NAME, VERSION

from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT


def build_doc():
    doc = Document()

    # ---- 样式设置 ----
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(11)
    style.paragraph_format.line_spacing = 1.5
    for level in range(1, 4):
        hs = doc.styles[f"Heading {level}"]
        hs.font.name = "Microsoft YaHei"
        hs.font.color.rgb = RGBColor(0, 100, 60)

    # ---- 封面 ----
    for _ in range(6):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(APP_NAME)
    run.font.size = Pt(36)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0, 140, 80)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"Version {VERSION}")
    run.font.size = Pt(18)
    run.font.color.rgb = RGBColor(80, 80, 80)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("用户说明文档")
    run.font.size = Pt(16)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(datetime.now().strftime("%Y 年 %m 月"))
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(120, 120, 120)

    doc.add_page_break()

    # ---- 目录页 ----
    doc.add_heading("目录", level=1)
    toc_items = [
        "1. 系统简介",
        "2. 安装指南",
        "   2.1 Windows 安装包部署",
        "   2.2 Docker 容器部署",
        "   2.3 从源码运行",
        "3. 快速上手",
        "4. 功能说明",
        "   4.1 知识库管理",
        "   4.2 智能搜索",
        "   4.3 RAG 智能问答",
        "   4.4 REST API 接口",
        "   4.5 插件扩展",
        "5. 配置说明",
        "   5.1 LLM 配置",
        "   5.2 Embedding 配置",
        "   5.3 RAG 参数",
        "6. API 接口参考",
        "7. 版本更新日志",
        "8. 常见问题",
    ]
    for item in toc_items:
        p = doc.add_paragraph(item)
        p.paragraph_format.space_after = Pt(2)
    doc.add_page_break()

    # ---- 1. 系统简介 ----
    doc.add_heading("1. 系统简介", level=1)
    doc.add_paragraph(
        f"{APP_NAME} 是一款本地部署的知识库管理系统，旨在帮助用户高效整理、检索和调用自有知识体系。"
        "系统支持多模态内容管理（文本、PDF、Word、Markdown、代码、图片等），"
        "内置基于 RAG（检索增强生成）的智能问答功能，提供桌面 GUI 和 REST API 两种访问方式。"
    )
    doc.add_heading("核心特性", level=2)
    features = [
        "多格式文档导入：PDF、DOCX、TXT、Markdown、HTML、代码文件、图片",
        "智能文本分块：按文档类型自动选择最优分块策略",
        "全文检索 + 语义搜索：SQLite FTS5 全文索引 + 向量相似度检索",
        "RAG 智能问答：基于知识库内容的上下文增强问答，标注引用来源",
        "版本控制：知识条目编辑时自动创建版本快照，支持一键恢复",
        "标签分类：灵活的标签体系，支持按标签筛选和批量导出",
        "REST API：完整的 RESTful 接口，支持 JWT 认证、分页、版本管理",
        "可配置 LLM：支持任意 OpenAI 兼容协议的供应商（DeepSeek、智谱、Moonshot 等）",
        "插件系统：可扩展的插件架构，支持自定义功能扩展",
        "暗色科幻界面：Matrix 风格的终端式视觉体验",
    ]
    for f in features:
        doc.add_paragraph(f, style="List Bullet")

    # ---- 2. 安装指南 ----
    doc.add_heading("2. 安装指南", level=1)
    doc.add_heading("2.1 Windows 安装包部署", level=2)
    doc.add_paragraph("适合不熟悉命令行的用户。")
    steps = [
        "双击 ShineHeKnowledge_vX.X.X_Setup.exe 运行安装向导",
        "选择安装目录，点击「下一步」完成安装",
        "安装完成后，桌面会出现 ShineHeKnowledge 快捷方式",
        "双击快捷方式启动应用",
        "首次使用请点击左下角「设置」按钮配置 API Key",
    ]
    for i, s in enumerate(steps, 1):
        doc.add_paragraph(f"{i}. {s}")

    doc.add_heading("2.2 Docker 容器部署", level=2)
    doc.add_paragraph("适合需要服务化部署或跨平台运行的用户。")
    doc.add_paragraph("确保已安装 Docker 和 Docker Compose，然后执行：")
    p = doc.add_paragraph("docker-compose up -d shinehe-api")
    p.style = doc.styles["Normal"]
    p.runs[0].font.name = "Consolas"
    doc.add_paragraph("API 服务将在 http://localhost:8000 启动，访问 /docs 查看 API 文档。")

    doc.add_heading("2.3 从源码运行", level=2)
    doc.add_paragraph("需要 Python 3.10+ 环境。")
    steps_src = [
        "git clone <repo-url> && cd knowledge-base",
        "pip install -r requirements.txt",
        "python main.py    # 启动桌面应用",
        "python run_api.py # 启动 API 服务",
    ]
    for s in steps_src:
        p = doc.add_paragraph(s)
        p.runs[0].font.name = "Consolas"

    # ---- 3. 快速上手 ----
    doc.add_heading("3. 快速上手", level=1)
    doc.add_heading("3.1 配置 API", level=2)
    doc.add_paragraph(
        "启动应用后，点击左侧「设置」按钮（或在 API 中调用 /api/auth/register 注册账户）。"
        "在设置界面填写："
    )
    config_items = [
        "供应商名称：如 deepseek、zhipu、moonshot 等",
        "API Key：从供应商获取的密钥",
        "API 地址：供应商的 OpenAI 兼容接口地址",
        "模型：如 deepseek-chat、glm-4-flash 等",
    ]
    for c in config_items:
        doc.add_paragraph(c, style="List Bullet")

    doc.add_heading("3.2 导入知识", level=2)
    doc.add_paragraph(
        "在知识库界面点击「导入文件」按钮，选择一个或多个文件（支持 PDF、Word、Markdown 等），"
        "可选填标签，点击「开始导入」。系统将自动解析文件内容、分块、向量化并存储。"
    )

    doc.add_heading("3.3 智能问答", level=2)
    doc.add_paragraph(
        "切换到「智能问答」页面，输入问题后按 Enter 发送。"
        "系统会自动检索最相关的知识内容，结合 LLM 生成回答，并标注引用来源。"
    )

    # ---- 4. 功能说明 ----
    doc.add_heading("4. 功能说明", level=1)
    doc.add_heading("4.1 知识库管理", level=2)
    doc.add_paragraph("支持以下操作：")
    mgmt = [
        "创建：手动输入或从文件导入",
        "编辑：修改标题、内容、标签",
        "版本控制：每次编辑自动创建版本快照，可查看历史版本并一键恢复",
        "分类与标签：支持多标签，按标签筛选",
        "批量导出：通过 API 按标签或 ID 批量导出为 JSON",
        "删除：同步清理数据库记录和向量索引",
    ]
    for m in mgmt:
        doc.add_paragraph(m, style="List Bullet")

    doc.add_heading("4.2 智能搜索", level=2)
    doc.add_paragraph(
        "搜索框支持关键词搜索。系统使用 SQLite FTS5 全文索引引擎，"
        "优先使用全文检索（支持中英文分词），若无精确匹配则自动降级为模糊匹配。"
        "搜索结果可按更新时间、创建时间、标题排序。"
    )

    doc.add_heading("4.3 RAG 智能问答", level=2)
    doc.add_paragraph(
        "RAG（Retrieval-Augmented Generation）工作流程：\n"
        "1. 用户输入问题\n"
        "2. 对问题进行向量化\n"
        "3. 在向量数据库中检索最相关的知识块（Top-K）\n"
        "4. 将检索结果作为上下文，结合问题构造 Prompt\n"
        "5. 调用 LLM 生成回答\n"
        "6. 返回回答及引用来源"
    )

    doc.add_heading("4.4 REST API 接口", level=2)
    doc.add_paragraph(
        "提供完整的 RESTful API，支持系统集成。"
        "所有接口需要 JWT 认证（登录获取 Token 后放在 Authorization Header 中）。"
        "支持分页查询、按标签/类型筛选、排序、版本管理、批量导出等操作。"
    )

    doc.add_heading("4.5 插件扩展", level=2)
    doc.add_paragraph(
        "在 src/plugins/ 目录下创建 .py 文件，实现 register(hook_registry) 函数即可注册插件。"
        "系统启动时自动扫描并加载 plugins 目录下的所有模块。"
        "支持注册自定义钩子函数，在知识创建、删除等事件时触发。"
    )

    # ---- 5. 配置说明 ----
    doc.add_heading("5. 配置说明", level=1)
    doc.add_paragraph("配置文件为项目根目录下的 config.yaml，也可通过 GUI 设置界面修改。")

    doc.add_heading("5.1 LLM 配置", level=2)
    table = doc.add_table(rows=7, cols=3)
    table.style = "Table Grid"
    headers = ["字段", "说明", "示例"]
    rows_data = [
        ["provider", "供应商名称（自定义标识）", "deepseek"],
        ["api_key", "API 密钥", "sk-xxx..."],
        ["base_url", "API 地址（必填）", "https://api.deepseek.com/v1"],
        ["model", "模型名称", "deepseek-chat"],
        ["temperature", "创造性程度 0-1", "0.7"],
        ["max_tokens", "最大输出 token 数", "2048"],
    ]
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
        for p in table.rows[0].cells[i].paragraphs:
            for r in p.runs:
                r.font.bold = True
    for i, row in enumerate(rows_data):
        for j, val in enumerate(row):
            table.rows[i + 1].cells[j].text = val

    doc.add_paragraph()
    doc.add_heading("常见供应商配置", level=3)
    providers = [
        ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
        ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
        ("Moonshot", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
        ("硅基流动", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3"),
        ("Ollama 本地", "http://localhost:11434/v1", "qwen2"),
    ]
    table2 = doc.add_table(rows=len(providers) + 1, cols=3)
    table2.style = "Table Grid"
    for i, h in enumerate(["供应商", "API 地址", "模型示例"]):
        table2.rows[0].cells[i].text = h
        for p in table2.rows[0].cells[i].paragraphs:
            for r in p.runs:
                r.font.bold = True
    for i, (name, url, model) in enumerate(providers):
        table2.rows[i + 1].cells[0].text = name
        table2.rows[i + 1].cells[1].text = url
        table2.rows[i + 1].cells[2].text = model

    doc.add_heading("5.2 Embedding 配置", level=2)
    doc.add_paragraph(
        "Embedding 模型用于将文本转换为向量，用于语义搜索。"
        "大多数供应商的 Embedding 接口与 LLM 共享同一地址和 Key，只需修改模型名即可。"
        "在设置界面勾选「与 LLM 使用相同供应商」可自动复用配置。"
    )

    doc.add_heading("5.3 RAG 参数", level=2)
    table3 = doc.add_table(rows=5, cols=3)
    table3.style = "Table Grid"
    rag_params = [
        ["top_k", "检索返回的知识块数量", "5"],
        ["chunk_size", "文本分块大小（字符数）", "500"],
        ["chunk_overlap", "分块重叠字符数", "50"],
        ["score_threshold", "相似度阈值（0-1）", "0.5"],
    ]
    for i, h in enumerate(["参数", "说明", "默认值"]):
        table3.rows[0].cells[i].text = h
        for p in table3.rows[0].cells[i].paragraphs:
            for r in p.runs:
                r.font.bold = True
    for i, row in enumerate(rag_params):
        for j, val in enumerate(row):
            table3.rows[i + 1].cells[j].text = val

    # ---- 6. API 接口参考 ----
    doc.add_heading("6. API 接口参考", level=1)
    doc.add_paragraph(f"API 文档地址：http://localhost:8000/docs")
    doc.add_paragraph("所有接口需在 Header 中携带 Authorization: Bearer <token>")

    api_table = doc.add_table(rows=17, cols=3)
    api_table.style = "Table Grid"
    api_headers = ["方法", "路径", "说明"]
    for i, h in enumerate(api_headers):
        api_table.rows[0].cells[i].text = h
        for p in api_table.rows[0].cells[i].paragraphs:
            for r in p.runs:
                r.font.bold = True
    api_routes = [
        ("POST", "/api/auth/register", "注册用户"),
        ("POST", "/api/auth/login", "登录获取 Token"),
        ("GET", "/api/health", "健康检查"),
        ("GET", "/api/knowledge", "知识列表（分页/筛选/排序）"),
        ("GET", "/api/knowledge/search?q=xxx", "搜索知识"),
        ("GET", "/api/knowledge/tags", "获取所有标签"),
        ("GET", "/api/knowledge/{id}", "知识详情"),
        ("POST", "/api/knowledge", "创建知识"),
        ("PUT", "/api/knowledge/{id}", "更新知识"),
        ("DELETE", "/api/knowledge/{id}", "删除知识"),
        ("GET", "/api/knowledge/{id}/versions", "版本历史"),
        ("POST", "/api/knowledge/{id}/versions/{v}/restore", "恢复版本"),
        ("POST", "/api/knowledge/export", "批量导出"),
        ("POST", "/api/chat/ask", "RAG 智能问答"),
        ("GET", "/api/chat/conversations", "对话列表"),
        ("GET", "/api/chat/conversations/{id}/messages", "对话消息"),
    ]
    for i, (method, path, desc) in enumerate(api_routes):
        api_table.rows[i + 1].cells[0].text = method
        api_table.rows[i + 1].cells[1].text = path
        api_table.rows[i + 1].cells[2].text = desc

    # ---- 7. 版本更新日志 ----
    doc.add_heading("7. 版本更新日志", level=1)
    doc.add_heading(f"v{VERSION} ({datetime.now().strftime('%Y-%m-%d')})", level=2)
    changelog = [
        "全新 Matrix 风格暗色科幻界面",
        "支持任意 OpenAI 兼容协议的 LLM 供应商",
        "SQLite FTS5 全文索引 + 模糊搜索",
        "知识条目版本控制（自动快照 + 一键恢复）",
        "完整的 RESTful API（JWT 认证、分页、CRUD、问答）",
        "可扩展插件系统",
        "Docker 容器化部署支持",
        "Windows 安装包",
    ]
    for c in changelog:
        doc.add_paragraph(c, style="List Bullet")

    # ---- 8. 常见问题 ----
    doc.add_heading("8. 常见问题", level=1)
    faqs = [
        ("Q: 首次启动提示缺少依赖？", "A: 请确保已安装 Python 3.10+，并运行 pip install -r requirements.txt 安装全部依赖。"),
        ("Q: 导入 PDF 文件时中文乱码？", "A: 系统使用 PyPDF2 解析 PDF，部分扫描版 PDF 可能无法正确提取文字。建议使用文字版 PDF。"),
        ("Q: API Key 填写后仍然报错？", "A: 请检查 API 地址是否正确（需要以 /v1 结尾），以及 API Key 是否有效。可在设置界面测试。"),
        ("Q: Docker 部署如何持久化数据？", "A: docker-compose.yml 已将 ./data 目录挂载到容器内，数据会保存在宿主机。"),
        ("Q: 如何切换 LLM 供应商？", "A: 在设置界面修改供应商名称、API 地址、Key 和模型名即可，所有供应商统一使用 OpenAI 兼容协议。"),
    ]
    for q, a in faqs:
        doc.add_paragraph(q).runs[0].font.bold = True
        doc.add_paragraph(a)

    # ---- 保存 ----
    os.makedirs("docs", exist_ok=True)
    filename = f"docs/{APP_NAME}_UserManual_v{VERSION}.docx"
    doc.save(filename)
    print(f"文档已生成: {filename}")
    return filename


if __name__ == "__main__":
    build_doc()
