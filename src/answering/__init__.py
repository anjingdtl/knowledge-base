"""Answer orchestration package (Phase-3 maintainability).

Public flow:
  Question → SearchService.execute (RetrievalOrchestrator) → SearchExecution
  → ContextBuilder / Generation → AnswerExecution
"""
from src.answering.models import AnswerExecution
from src.answering.service import AnswerService

__all__ = ["AnswerExecution", "AnswerService"]
