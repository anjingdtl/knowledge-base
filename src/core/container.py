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
    block_store: "BlockStore" = field(default=None)  # noqa: F821

    # --- 仓库层（Phase 1.2 新增） ---
    knowledge_repo: "KnowledgeRepository" = field(default=None, repr=False)  # noqa: F821
    conversation_repo: "ConversationRepository" = field(default=None, repr=False)  # noqa: F821
    wiki_repo: "WikiRepository" = field(default=None, repr=False)  # noqa: F821
    graph_repo: "GraphRepository" = field(default=None, repr=False)  # noqa: F821
    block_repo: "BlockRepository" = field(default=None, repr=False)  # noqa: F821
    entity_ref_repo: "EntityRefRepository" = field(default=None, repr=False)  # noqa: F821
    category_repo: "CategoryRepository" = field(default=None, repr=False)  # noqa: F821
    job_repo: "JobRepository" = field(default=None, repr=False)  # noqa: F821

    # --- 仓库层（Phase 2 新增） ---
    tag_relation_repo: "TagRelationRepository" = field(default=None, repr=False)  # noqa: F821
    property_schema_repo: "PropertySchemaRepository" = field(default=None, repr=False)  # noqa: F821
    operation_log_repo: "OperationLogRepository" = field(default=None, repr=False)  # noqa: F821

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
    _search_service: Optional[object] = field(default=None, repr=False)
    _file_graph_service: Optional[object] = field(default=None, repr=False)

    # --- Phase 2 业务服务 (lazy init) ---
    _unified_graph: Optional[object] = field(default=None, repr=False)
    _tag_hierarchy: Optional[object] = field(default=None, repr=False)
    _property_schema: Optional[object] = field(default=None, repr=False)
    _effective_properties: Optional[object] = field(default=None, repr=False)

    # --- Phase 3 业务服务 (lazy init) ---
    _query_executor: Optional[object] = field(default=None, repr=False)
    _graph_traversal: Optional[object] = field(default=None, repr=False)
    _query_explainer: Optional[object] = field(default=None, repr=False)
    _agentic_router: Optional[object] = field(default=None, repr=False)

    # --- 操作安全服务 (lazy init) ---
    _operation_log: Optional[object] = field(default=None, repr=False)

    _initialized_services: list = field(default_factory=list, repr=False)

    def _track_service(self, attr_name: str):
        if attr_name not in self._initialized_services:
            self._initialized_services.append(attr_name)

    @property
    def indexer(self):
        if self._indexer is None:
            from src.services.indexer import IndexerService
            self._indexer = IndexerService(self.db, self.vectorstore, self.embedding, self.config)
            self._track_service("_indexer")
        return self._indexer

    @property
    def hybrid_search(self):
        if self._hybrid_search is None:
            from src.services.hybrid_search import HybridSearcher
            self._hybrid_search = HybridSearcher(self.db, self.vectorstore, self.config)
            self._track_service("_hybrid_search")
        return self._hybrid_search

    @property
    def rag_pipeline(self):
        if self._rag_pipeline is None:
            from src.services.rag_pipeline import RAGService
            self._rag_pipeline = RAGService()
            self._track_service("_rag_pipeline")
        return self._rag_pipeline

    @property
    def query_rewriter(self):
        if self._query_rewriter is None:
            from src.services.query_rewriter import QueryRewriter
            self._query_rewriter = QueryRewriter(self.llm, self.config)
            self._track_service("_query_rewriter")
        return self._query_rewriter

    @property
    def reranker(self):
        if self._reranker is None:
            from src.services.reranker import LLMReranker
            self._reranker = LLMReranker(self.llm, self.config)
            self._track_service("_reranker")
        return self._reranker

    @property
    def wiki_compiler(self):
        if self._wiki_compiler is None:
            from src.services.wiki_compiler import WikiCompiler
            self._wiki_compiler = WikiCompiler(self.db, self.llm, self.config)
            self._track_service("_wiki_compiler")
        return self._wiki_compiler

    @property
    def wiki_workflow(self):
        if self._wiki_workflow is None:
            from src.services.wiki_workflow import WikiWorkflowService
            self._wiki_workflow = WikiWorkflowService(self.db)
            self._track_service("_wiki_workflow")
        return self._wiki_workflow

    @property
    def graph_builder(self):
        if self._graph_builder is None:
            from src.services.graph_builder import GraphBuilder
            self._graph_builder = GraphBuilder(self.db, self.llm, self.config)
            self._track_service("_graph_builder")
        return self._graph_builder

    @property
    def librarian(self):
        if self._librarian is None:
            from src.services.librarian import LibrarianService
            self._librarian = LibrarianService()
            self._track_service("_librarian")
        return self._librarian

    @property
    def search_service(self):
        if self._search_service is None:
            from src.services.search_service import SearchService
            self._search_service = SearchService(
                self.config, self.db, self.block_store, self.embedding, self.llm
            )
            self._track_service("_search_service")
        return self._search_service

    @property
    def file_graph_service(self):
        if self._file_graph_service is None:
            from src.services.file_graph import FileGraphService
            self._file_graph_service = FileGraphService(
                self.config, self.db, self.block_store, self.embedding
            )
            self._track_service("_file_graph_service")
        return self._file_graph_service

    # --- Phase 2 lazy services ---

    @property
    def unified_graph(self):
        if self._unified_graph is None:
            from src.services.unified_graph import UnifiedGraphService
            self._unified_graph = UnifiedGraphService(db=self.db)
            self._track_service("_unified_graph")
        return self._unified_graph

    @property
    def tag_hierarchy(self):
        if self._tag_hierarchy is None:
            from src.services.tag_hierarchy import TagHierarchyService
            self._tag_hierarchy = TagHierarchyService(db=self.db, repo=self.tag_relation_repo)
            self._track_service("_tag_hierarchy")
        return self._tag_hierarchy

    @property
    def property_schema(self):
        if self._property_schema is None:
            from src.services.property_schema import PropertySchemaService
            self._property_schema = PropertySchemaService(db=self.db, repo=self.property_schema_repo)
            self._track_service("_property_schema")
        return self._property_schema

    @property
    def effective_properties(self):
        if self._effective_properties is None:
            from src.services.effective_properties import EffectivePropertyService
            self._effective_properties = EffectivePropertyService(db=self.db, schema_service=self.property_schema)
            self._track_service("_effective_properties")
        return self._effective_properties

    @property
    def query_executor(self):
        if self._query_executor is None:
            from src.services.query_executor import QueryExecutor
            self._query_executor = QueryExecutor(db=self.db)
            self._track_service("_query_executor")
        return self._query_executor

    @property
    def graph_traversal(self):
        if self._graph_traversal is None:
            from src.services.graph_traversal import GraphTraversalService
            self._graph_traversal = GraphTraversalService(db=self.db)
            self._track_service("_graph_traversal")
        return self._graph_traversal

    @property
    def query_explainer(self):
        if self._query_explainer is None:
            from src.services.query_explainer import QueryExplainer
            self._query_explainer = QueryExplainer()
            self._track_service("_query_explainer")
        return self._query_explainer

    @property
    def agentic_router(self):
        if self._agentic_router is None:
            from src.services.agentic_router import AgenticRouter
            self._agentic_router = AgenticRouter(db=self.db, llm=self.llm)
            self._track_service("_agentic_router")
        return self._agentic_router

    @property
    def operation_log(self):
        if self._operation_log is None:
            from src.services.operation_log import OperationLogService
            self._operation_log = OperationLogService(repo=self.operation_log_repo)
            self._track_service("_operation_log")
        return self._operation_log


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
    # 如果已有活跃连接（如测试 fixture 已 connect），保留现有连接，不覆盖
    from src.services.db import Database
    try:
        Database.get_conn()
        logger.info("Database already connected, reusing existing connection")
    except Exception:
        db_path = config.get_db_path()
        Database.connect(str(db_path))
        logger.info("Database connected: %s", db_path)

    # 3. VectorStore（注入 Database 实例）
    from src.services.vectorstore import VectorStore
    vectorstore = VectorStore(db=Database)
    logger.info("VectorStore ready")

    from src.services.block_store import BlockStore
    block_store = BlockStore(db=Database)
    logger.info("BlockStore ready")

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
        block_store=block_store,
        embedding=embedding,
        llm=llm,
    )

    # 初始化仓库层
    from src.repositories.knowledge_repo import KnowledgeRepository
    from src.repositories.conversation_repo import ConversationRepository
    from src.repositories.wiki_repo import WikiRepository
    from src.repositories.graph_repo import GraphRepository
    from src.repositories.block_repo import BlockRepository
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.repositories.category_repo import CategoryRepository
    from src.repositories.job_repo import JobRepository
    container.knowledge_repo = KnowledgeRepository(db=Database)
    container.conversation_repo = ConversationRepository(db=Database)
    container.wiki_repo = WikiRepository(db=Database)
    container.graph_repo = GraphRepository(db=Database)
    container.block_repo = BlockRepository(db=Database)
    container.entity_ref_repo = EntityRefRepository(db=Database)
    container.category_repo = CategoryRepository(db=Database)
    container.job_repo = JobRepository(db=Database)

    # Phase 2 仓库
    from src.repositories.tag_relation_repo import TagRelationRepository
    from src.repositories.property_schema_repo import PropertySchemaRepository
    from src.repositories.operation_log_repo import OperationLogRepository
    container.tag_relation_repo = TagRelationRepository(db=Database)
    container.property_schema_repo = PropertySchemaRepository(db=Database)
    container.operation_log_repo = OperationLogRepository(db=Database)

    logger.info("Repositories initialized")

    # 将容器注入 Database 类，保持旧代码兼容
    Database._container = container

    return container


def shutdown_container(container: AppContainer):
    """关闭容器，释放资源"""
    try:
        for attr_name in getattr(container, '_initialized_services', []):
            svc = getattr(container, attr_name, None)
            if svc and hasattr(svc, 'close'):
                try:
                    svc.close()
                except Exception:
                    pass
        if container.db is not None:
            container.db.close()
            logger.info("Database closed")
    except Exception as e:
        logger.warning("Error during container shutdown: %s", e)
