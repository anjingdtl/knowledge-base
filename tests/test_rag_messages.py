"""Tests for RAG answer prompt/message construction."""

from src.services.rag_pipeline import RAG_SYSTEM_PROMPT, build_rag_messages


def test_rag_prompt_requires_combined_reasoning():
    assert "组合推理" in RAG_SYSTEM_PROMPT
    assert "证据不足" in RAG_SYSTEM_PROMPT


def test_build_rag_messages_keeps_history_before_grounded_question():
    history = [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮回答"},
    ]

    messages = build_rag_messages("第七届创智杯的省份可获得最高团队奖个数是多少", "来源内容", history)

    assert messages[0]["role"] == "system"
    assert messages[1:3] == history
    assert messages[-1]["role"] == "user"
    assert "来源内容" in messages[-1]["content"]
    assert "第七届创智杯" in messages[-1]["content"]
    assert "先拆解问题" in messages[-1]["content"]
