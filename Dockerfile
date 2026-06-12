# ---- Stage 1: Base ----
FROM python:3.12-slim AS base

WORKDIR /app

# editable install 需要版本文件和包目录在构建上下文中
COPY pyproject.toml ./
COPY src ./src

# 安装核心依赖（不含可选 extras）
RUN pip install --no-cache-dir -e .

# ---- Stage 2: API Server ----
FROM base AS api

RUN pip install --no-cache-dir -e ".[api,parsers,wiki,graph]"

COPY . .

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

# 非 root 运行
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["python", "run_api.py"]

# ---- Stage 3: MCP Server ----
FROM base AS mcp

RUN pip install --no-cache-dir -e "."

COPY . .

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "run_mcp.py"]
