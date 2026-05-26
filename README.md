# ShineHeKnowledge

本地部署的知识库系统，支持多模态文档管理、RAG 智能问答，提供桌面 GUI、REST API 和 MCP Server 三种访问方式。

## 快速开始

### pip 安装（推荐 MCP 用户）

```bash
cd knowledge-base
pip install -e .                # 开发模式安装（含 MCP 核心）
pip install -e ".[parsers]"    # 含文件解析（PDF/DOCX/图片等）
pip install -e ".[all]"        # 含 GUI + API + 解析器

shinehe-mcp                    # 启动 MCP Server（stdio 模式）
shinehe-mcp -t streamable-http --port 9000   # HTTP 模式
```

### Windows 安装包

1. 下载 `ShineHeKnowledge_v1.0.0_Setup.exe`
2. 双击运行安装向导
3. 启动后在设置界面配置 API Key

### 从源码运行

```bash
pip install -r requirements.txt
python main.py      # 桌面应用
python run_api.py   # API 服务
python run_mcp.py   # MCP Server
```

### Docker

```bash
docker-compose up -d shinehe-api
```

API 文档: http://localhost:8000/docs

## 配置

编辑 `config.yaml` 或在 GUI 设置界面配置。支持任意 OpenAI 兼容供应商：

| 供应商 | API 地址 | 模型示例 |
|--------|----------|----------|
| DeepSeek | https://api.deepseek.com/v1 | deepseek-chat |
| 智谱 GLM | https://open.bigmodel.cn/api/paas/v4 | glm-4-flash |
| Moonshot | https://api.moonshot.cn/v1 | moonshot-v1-8k |
| 硅基流动 | https://api.siliconflow.cn/v1 | deepseek-ai/DeepSeek-V3 |
| Ollama 本地 | http://localhost:11434/v1 | qwen2 |

## API 接口

认证: `POST /api/auth/register` → 获取 Bearer Token

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/knowledge | 列表（分页/筛选/排序） |
| GET | /api/knowledge/search?q=xxx | 搜索 |
| POST | /api/knowledge | 创建 |
| PUT | /api/knowledge/{id} | 更新（自动版本快照） |
| DELETE | /api/knowledge/{id} | 删除 |
| GET | /api/knowledge/{id}/versions | 版本历史 |
| POST | /api/knowledge/{id}/versions/{v}/restore | 恢复版本 |
| POST | /api/knowledge/export | 批量导出 |
| POST | /api/chat/ask | RAG 问答 |

## 运行测试

```bash
pytest tests/ -v
```

## MCP 接入各 Agent

### 一键配置

```bash
python scripts/setup_mcp.py
```

脚本自动检测已安装的 Agent（Claude Code、Cursor、Cline、Windsurf、Roo Code），交互选择后写入配置。

### 手动配置

配置模板位于 `mcp_config_templates/` 目录，各文件包含完整的 JSON 配置片段：

- `claude-code.json` — Claude Code（stdio / HTTP 两种模式）
- `cursor.json` — Cursor
- `cline.json` — Cline（VS Code 扩展，含 alwaysAllow）
- `windsurf.json` — Windsurf
- `roo-code.json` — Roo Code
- `http-remote.json` — streamable-http 远程接入（局域网共享）

各模板中的 `SHINEHE_HOME` 替换为实际项目路径即可。

### 配置文件位置速查

| Agent | 配置文件位置 |
|-------|-------------|
| Claude Code | `claude mcp add shinehe-kb -- shinehe-mcp` 或编辑 `~/.claude.json` |
| Cursor | `~/.cursor/mcp.json` |
| Cline | `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json` |
| Windsurf | `%APPDATA%\WindSurf\mcp_settings.json` |
| Roo Code | `%APPDATA%\Code\User\globalStorage\rooveterinaryinc.roo-cline\settings\cline_mcp_settings.json` |

### 环境变量

| 变量 | 说明 |
|------|------|
| `SHINEHE_HOME` | 知识库项目根目录（含 config.yaml 和 data/），覆盖自动检测 |

## 版本发布流程

1. 修改 `src/version.py` 中的 VERSION
2. 运行 `python scripts/build_docs.py` — 生成用户说明文档
3. 运行 `python scripts/build_windows.py` — 打 Windows 安装包
4. 运行 `python scripts/build_docker.py` — 打 Docker 镜像

## 项目结构

```
knowledge-base/
├── main.py                # 桌面应用入口
├── run_api.py             # API 服务入口
├── src/
│   ├── version.py         # 版本号（唯一来源）
│   ├── api/               # REST API
│   ├── gui/               # PySide6 桌面界面
│   ├── models/            # 数据模型
│   ├── plugins/           # 插件系统
│   └── services/          # 核心服务
├── scripts/
│   ├── build_docs.py      # 生成 DOCX 文档
│   ├── build_windows.py   # 打 Windows 安装包
│   └── build_docker.py    # 打 Docker 镜像
├── installer/setup.iss    # Inno Setup 安装脚本
├── docs/                  # 生成的用户文档
├── tests/                 # 测试用例
├── Dockerfile
└── docker-compose.yml
```
