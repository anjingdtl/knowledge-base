"""RAG 服务入口 — 向后兼容层

所有实现已合并到 rag_pipeline.py，此文件仅保留 import 别名。
新代码请直接使用: from src.services.rag_pipeline import RAGService, RagPipeline
"""

# 向后兼容：from src.services.rag import RAGService 仍然有效
from src.services.rag_pipeline import PipelineStage, RagContext, RagPipeline, RAGService, StageRegistry

__all__ = ["RAGService", "RagPipeline", "RagContext", "PipelineStage", "StageRegistry"]
