FROM python:3.12-slim

LABEL name="ShineHeKnowledge" \
      version="1.2.0" \
      description="本地知识库系统 - 多模态文档管理 + RAG 智能问答"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "run_api.py"]
