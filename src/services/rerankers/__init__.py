"""可插拔重排序器包 — 提供多种重排序实现

通过工厂函数 create_reranker() 根据配置创建合适的重排序器:
    - ApiReranker: 调用专用重排序 API (SiliconFlow, Cohere 等)
    - LocalCrossEncoderReranker: 本地 sentence-transformers 交叉编码器
    - LLMFallbackReranker: LLM 打分回退方案
    - DisabledReranker: 禁用重排序
"""
from __future__ import annotations

from src.services.rerankers.base import Reranker
from src.services.rerankers.factory import create_reranker

__all__ = ["Reranker", "create_reranker"]
