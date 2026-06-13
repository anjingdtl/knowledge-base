from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer
from src.models.chat import ChatMessage, Conversation

chat_router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(_check_auth)])


class QuestionReq(BaseModel):
    question: str
    conversation_id: Optional[str] = None


@chat_router.post("/ask")
def ask_question(data: QuestionReq, container: AppContainer = Depends(get_container)):
    result = container.rag_pipeline.query(data.question)
    sources = _normalize_sources(result.get("sources", []))
    conv_id = data.conversation_id
    if not conv_id:
        conv = Conversation(title=data.question[:30])
        container.db.insert_conversation(conv.to_row())
        conv_id = conv.id
    user_msg = ChatMessage(conversation_id=conv_id, role="user", content=data.question)
    container.db.insert_message(user_msg.to_row())
    ai_msg = ChatMessage(
        conversation_id=conv_id,
        role="assistant",
        content=result["answer"],
        sources=sources,
        source_graph=result.get("source_graph", {"nodes": [], "edges": []}),
    )
    container.db.insert_message(ai_msg.to_row())
    # 构建诊断信息 — 从管线各阶段收集
    diagnostics = _build_diagnostics(result)

    return {
        "conversation_id": conv_id,
        "answer": result["answer"],
        "sources": sources,
        "source_graph": result.get("source_graph", {"nodes": [], "edges": []}),
        "diagnostics": diagnostics,
    }


def _normalize_sources(sources: list[dict]) -> list[dict]:
    normalized = []
    for source in sources or []:
        metadata = source.get("metadata") or {}
        block_id = (
            source.get("block_id")
            or source.get("chunk_id")
            or metadata.get("block_id")
            or source.get("id")
        )
        knowledge_id = source.get("knowledge_id") or metadata.get("knowledge_id")
        snippet = source.get("snippet") or source.get("text") or source.get("content") or ""
        normalized.append({
            **source,
            "block_id": block_id,
            "knowledge_id": knowledge_id,
            "title": source.get("title") or metadata.get("title") or "",
            "snippet": snippet,
            "score": source.get("score", source.get("distance")),
        })
    return normalized


def _build_diagnostics(result: dict) -> dict:
    """从管线结果中提取检索诊断信息"""
    route = result.get("route", {})
    query_plan = result.get("query_plan", {})
    warnings = result.get("warnings", [])
    sources = result.get("sources", [])
    wiki_context = result.get("wiki_context", "")
    source_graph = result.get("source_graph", {})

    # 统计 token 估算（来源文本长度 / 4 近似）
    evidence_chars = sum(len(s.get("text_preview", "") or s.get("snippet", "")) for s in sources)
    if wiki_context:
        evidence_chars += len(wiki_context)

    # 被丢弃的候选（得分低于阈值或被去重移除）
    dropped = []
    for w in warnings:
        if "empty" in w.lower() or "fallback" in w.lower():
            dropped.append({"reason": w})

    return {
        "route": {
            "mode": route.get("mode", "unknown") if isinstance(route, dict) else str(route),
            "explanation": route.get("explanation", "") if isinstance(route, dict) else "",
        },
        "retrieval": {
            "total_sources": len(sources),
            "wiki_hits": 1 if wiki_context else 0,
            "graph_nodes": source_graph.get("node_count", 0),
            "graph_truncated": source_graph.get("truncated", False),
            "evidence_chars": evidence_chars,
            "evidence_tokens_est": evidence_chars // 4,
        },
        "query_plan": query_plan,
        "dropped_candidates": dropped,
        "warnings": warnings,
    }


@chat_router.get("/conversations")
def list_conversations(limit: int = 50, container: AppContainer = Depends(get_container)):
    return {"conversations": container.db.list_conversations(limit=limit)}


@chat_router.get("/conversations/{conv_id}/messages")
def get_messages(conv_id: str, container: AppContainer = Depends(get_container)):
    return {"messages": container.db.get_messages(conv_id)}
