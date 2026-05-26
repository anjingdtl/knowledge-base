"""Embedding 服务 — 基于 OpenAI 兼容协议，支持任意供应商"""
import time
from src.utils.config import Config


class EmbeddingService:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        from openai import OpenAI
        api_key = Config.get("embedding.api_key") or Config.get("llm.api_key", "") or "no-key"
        base_url = Config.get("embedding.base_url") or Config.get("llm.base_url") or None
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
        return self._client

    def embed(self, text: str) -> list[float]:
        result = self.embed_batch([text])
        return result[0]

    def embed_batch(self, texts: list[str], batch_size: int = 20) -> list[list[float]]:
        client = self._get_client()
        model = Config.get("embedding.model", "")
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = client.embeddings.create(input=batch, model=model)
            for item in response.data:
                all_embeddings.append(item.embedding)
            if i + batch_size < len(texts):
                time.sleep(0.5)
        return all_embeddings

    def reset_client(self):
        self._client = None
