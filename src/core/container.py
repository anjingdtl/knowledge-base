"""轻量依赖注入容器 — 管理所有服务实例及其依赖关系

用法:
    container = create_container()           # 默认配置
    container = create_container("/path")    # 指定 config 路径

    # 访问服务
    container.db.list_knowledge()
    container.llm.chat([...])
    container.config.get("llm.model")

设计原则:
    - 纯手工容器，不引入框架依赖
    - 按依赖拓扑顺序创建服务
    - 每个服务通过属性暴露，替代全局单例
    - 旧代码过渡期可通过 compat 属性访问 Database 单例
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AppContainer:
    """应用容器 — 持有所有服务实例

    服务按依赖拓扑排列:
        Config → Database → VectorStore → EmbeddingService / LLMService → 上层服务
    """

    # --- 基础设施 ---
    config: "Config" = field(default=None)  # noqa: F821
    db: "Database" = field(default=None)  # noqa: F821

    # --- 存储 ---
    vectorstore: "VectorStore" = field(default=None)  # noqa: F821

    # --- 仓库层（Phase 1.2 新增） ---
    knowledge_repo: "KnowledgeRepository" = field(default=None, repr=False)  # noqa: F821
    conversation_repo: "ConversationRepository" = field(default=None, repr=False)  # noqa: F821
    wiki_repo: "WikiRepository" = field(default=None, repr=False)  # noqa: F821
    graph_repo: "GraphRepository" = field(default=None, repr=False)  # noqa: F821
    category_repo: "CategoryRepository" = field(default=None, repr=False)  # noqa: F821
    job_repo: "JobRepository" = field(default=None, repr=False)  # noqa: F821

    # --- AI 服务 ---
    embedding: "EmbeddingService" = field(default=None)  # noqa: F821
    llm: "LLMService" = field(default=None)  # noqa: F821

    # --- 业务服务 (lazy init) ---
    _indexer: Optional[object] = field(default=None, repr=False)
    _hybrid_search: Optional[object] = field(default=None, repr=False)
    _rag_pipeline: Optional[object] = field(default=None, repr=False)
    _query_rewriter: Optional[object] = field(default=None, repr=False)
    _reranker: Optional[object] = field(default=None, repr=False)
    _wiki_compiler: Optional[object] = field(default=None, repr=False)
    _wiki_workflow: Optional[object] = field(default=None, repr=False)
    _graph_builder: Optional[object] = field(default=None, repr=False)
    _librarian: Optional[object] = field(default=None, repr=False)

    @property
    def indexer(self):
        if self._indexer is None:
            from src.services.indexer import IndexerService
            self._indexer = IndexerService(self.db, self.vectorstore, self.embedding, self.config)
        return self._indexer

    @property
    def hybrid_search(self):
        if self._hybrid_search is None:
            from src.services.hybrid_search import HybridSearcher
            self._hybrid_search = HybridSearcher(self.db, self.vectorstore, self.config)
        return self._hybrid_search

    @property
    def rag_pipeline(self):
        if self._rag_pipeline is None:
            from src.services.rag_pipeline import RagPipeline
            self._rag_pipeline = RagPipeline(self)
        return self._rag_pipeline

    @property
    def query_rewriter(self):
        if self._query_rewriter is None:
            from src.services.query_rewriter import QueryRewriter
            self._query_rewriter = QueryRewriter(self.llm, self.config)
        return self._query_rewriter

    @property
    def reranker(self):
        if self._reranker is None:
            from src.services.reranker import LLMReranker
            self._reranker = LLMReranker(self.llm, self.config)
        return self._reranker

    @property
    def wiki_compiler(self):
        if self._wiki_compiler is None:
            from src.services.wiki_compiler import WikiCompiler
            self._wiki_compiler = WikiCompiler(self.db, self.llm, self.config)
        return self._wiki_compiler

    @property
    def wiki_workflow(self):
        if self._wiki_workflow is None:
            from src.services.wiki_workflow import WikiWorkflowService
            self._wiki_workflow = WikiWorkflowService(self.db)
        return self._wiki_workflow

    @property
    def graph_builder(self):
        if self._graph_builder is None:
            from src.services.graph_builder import GraphBuilder
            self._graph_builder = GraphBuilder(self.db, self.llm, self.config)
        return self._graph_builder

    @property
    def librarian(self):
        if self._librarian is None:
            from src.services.librarian import LibrarianService
            self._librarian = LibrarianService(self.db, self.llm, self.config)
        return self._librarian


def create_container(config_path: str | None = None) -> AppContainer:
    """创建并初始化应用容器

    按依赖拓扑顺序构建服务:
    1. Config — 加载配置
    2. Database — 连接 SQLite
    3. VectorStore — 初始化向量存储
    4. EmbeddingService / LLMService — AI 服务
    """
    # 1. Config
    from src.utils.config import Config
    config = Config()
    config.load(config_path)
    logger.info("Config loaded")

    # 2. Database（使用类方法连接，cls._conn 全局共享）
    from src.services.db import Database
    db_path = config.get_db_path()
    Database.connect(str(db_path))
    logger.info("Database connected: %s", db_path)

    # 3. VectorStore（注入 Database 实例）
    from src.services.vectorstore import VectorStore
    vectorstore = VectorStore(db=Database)
    logger.info("VectorStore ready")

    # 4. Embedding / LLM
    from src.services.embedding import EmbeddingService
    from src.services.llm import LLMService
    embedding = EmbeddingService(config)
    llm = LLMService(config)
    logger.info("AI services initialized")

    container = AppContainer(
        config=config,
        db=Database,
        vectorstore=vectorstore,
        embedding=embedding,
        llm=llm,
    )

    # 初始化仓库层
    from src.repositories.knowledge_repo import KnowledgeRepository
    from src.repositories.conversation_repo import ConversationRepository
    from src.repositories.wiki_repo import WikiRepository
    from src.repositories.graph_repo import GraphRepository
    from src.repositories.category_repo import CategoryRepository
    from src.repositories.job_repo import JobRepository
    container.knowledge_repo = KnowledgeRepository(db=Database)
    container.conversation_repo = ConversationRepository(db=Database)
    container.wiki_repo = WikiRepository(db=Database)
    container.graph_repo = GraphRepository(db=Database)
    container.category_repo = CategoryRepository(db=Database)
    container.job_repo = JobRepository(db=Database)
    logger.info("Repositories initialized")

    # 将容器注入 Database 类，保持旧代码兼容
    Database._container = container

    return container


def shutdown_container(container: AppContainer):
    """关闭容器，释放资源"""
    try:
        if container.db is not None:
            container.db.close()
            logger.info("Database closed")
    except Exception as e:
        logger.warning("Error closing database: %s", e)
