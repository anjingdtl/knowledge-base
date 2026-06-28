from src.api.routes.auth import auth_router
from src.api.routes.chat import chat_router
from src.api.routes.graph import graph_router
from src.api.routes.jobs import jobs_router
from src.api.routes.knowledge import kb_router, refs_router
from src.api.routes.maintenance import maintenance_router
from src.api.routes.properties import properties_router
from src.api.routes.search import query_router
from src.api.routes.settings import settings_router
from src.api.routes.tags import tags_router
from src.api.routes.wiki import wiki_router

__all__ = [
    "auth_router",
    "kb_router",
    "refs_router",
    "chat_router",
    "wiki_router",
    "jobs_router",
    "graph_router",
    "tags_router",
    "properties_router",
    "query_router",
    "settings_router",
    "maintenance_router",
]
