"""Answer orchestration package (maintainability closure WP2).

Public flow:
  Question → SearchService.execute (RetrievalOrchestrator) → SearchExecution
  → assemble_answer_payload → AnswerExecution
"""
from src.answering.assembler import assemble_answer_payload
from src.answering.models import AnswerExecution
from src.answering.service import AnswerService

__all__ = ["AnswerExecution", "AnswerService", "assemble_answer_payload"]
