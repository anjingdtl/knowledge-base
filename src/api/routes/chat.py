from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer
from src.models.chat import Conversation, ChatMessage

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
    return {
        "conversation_id": conv_id,
        "answer": result["answer"],
        "sources": sources,
        "source_graph": result.get("source_graph", {"nodes": [], "edges": []}),
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


@chat_router.get("/conversations")
def list_conversations(limit: int = 50, container: AppContainer = Depends(get_container)):
    return {"conversations": container.db.list_conversations(limit=limit)}


@chat_router.get("/conversations/{conv_id}/messages")
def get_messages(conv_id: str, container: AppContainer = Depends(get_container)):
    return {"messages": container.db.get_messages(conv_id)}
