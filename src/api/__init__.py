"""FastAPI 应用入口"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.services.db import Database
from src.utils.config import Config
from src.core.container import create_container, shutdown_container
from src.version import APP_NAME, VERSION
from src.api.routes import auth_router, kb_router, chat_router, wiki_router, jobs_router, refs_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = create_container()
    app.state.container = container
    yield
    shutdown_container(container)


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{APP_NAME} API",
        description=f"{APP_NAME} v{VERSION} RESTful API",
        version=VERSION,
        lifespan=lifespan,
    )

    # Read allowed origins from config; fall back to localhost-only defaults.
    # Wildcard ("*") with credentials is insecure — browsers would reject it
    # anyway, and silent API-level acceptance leaks tokens to any origin.
    allowed_origins = Config.get("api.cors_origins", ["http://localhost:8000"])
    if "*" in allowed_origins:
        logger.warning(
            "CORS wildcard detected in api.cors_origins — "
            "allow_credentials will be disabled for safety. "
            "Set explicit origins to enable credential forwarding."
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(auth_router, prefix="/api")
    app.include_router(kb_router, prefix="/api")
    app.include_router(refs_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(wiki_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {"status": "online", "name": APP_NAME, "version": VERSION, "nodes": Database.count_knowledge()}

    return app
