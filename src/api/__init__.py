"""FastAPI 应用入口"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.services.db import Database
from src.utils.config import Config
from src.version import APP_NAME, VERSION
from src.api.routes import auth_router, kb_router, chat_router, wiki_router, jobs_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Config.load()
    Database.connect()
    yield
    Database.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{APP_NAME} API",
        description=f"{APP_NAME} v{VERSION} RESTful API",
        version=VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router, prefix="/api")
    app.include_router(kb_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(wiki_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {"status": "online", "name": APP_NAME, "version": VERSION, "nodes": Database.count_knowledge()}

    return app
