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
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.repositories.agent_memory_repo import AgentMemoryRepository
    from src.repositories.block_repo import BlockRepository
    from src.repositories.category_repo import CategoryRepository
    from src.repositories.conversation_repo import ConversationRepository
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.repositories.graph_repo import GraphRepository
    from src.repositories.indexed_file_repo import IndexedFileRepository
    from src.repositories.job_repo import JobRepository
    from src.repositories.knowledge_repo import KnowledgeRepository
    from src.repositories.operation_log_repo import OperationLogRepository
    from src.repositories.property_schema_repo import PropertySchemaRepository
    from src.repositories.tag_relation_repo import TagRelationRepository
    from src.repositories.wiki_repo import WikiRepository
    from src.services.block_store import BlockStore
    from src.services.db import Database
    from src.services.embedding import EmbeddingService
    from src.services.graph_backend import GraphBackend
    from src.services.llm import LLMService
    from src.services.vectorstore import VectorStore
    from src.utils.config import Config

logger = logging.getLogger(__name__)

# 模块级引用 — 任何代码可通过 get_active_container() 获取当前容器
_active_container: "AppContainer | None" = None


def get_active_container() -> "AppContainer | None":
    """获取当前活跃的 DI 容器（替代旧的 Database._container 反向引用）。"""
    return _active_container


@dataclass
class AppContainer:
    """应用容器 — 持有所有服务实例

    服务按依赖拓扑排列:
        Config → Database → VectorStore → GraphBackend → EmbeddingService / LLMService → 上层服务
    """

    # --- 基础设施 ---
    config: "Config"
    db: "Database"

    # --- 存储 ---
    vectorstore: "VectorStore"
    block_store: "BlockStore"

    # --- 图后端（插件式） ---
    graph_backend: "GraphBackend"

    # --- 仓库层（Phase 1.2 新增） ---
    knowledge_repo: "KnowledgeRepository" = field(init=False, repr=False)
    conversation_repo: "ConversationRepository" = field(init=False, repr=False)
    wiki_repo: "WikiRepository" = field(init=False, repr=False)
    graph_repo: "GraphRepository" = field(init=False, repr=False)
    block_repo: "BlockRepository" = field(init=False, repr=False)
    entity_ref_repo: "EntityRefRepository" = field(init=False, repr=False)
    category_repo: "CategoryRepository" = field(init=False, repr=False)
    job_repo: "JobRepository" = field(init=False, repr=False)

    # --- 仓库层（Phase 2 新增） ---
    tag_relation_repo: "TagRelationRepository" = field(init=False, repr=False)
    property_schema_repo: "PropertySchemaRepository" = field(init=False, repr=False)
    operation_log_repo: "OperationLogRepository" = field(init=False, repr=False)

    # --- Phase 4 新增 ---
    agent_memory_repo: "AgentMemoryRepository" = field(init=False, repr=False)

    # --- M3: 路径索引 ---
    indexed_file_repo: "IndexedFileRepository" = field(init=False, repr=False)

    # --- AI 服务 ---
    embedding: "EmbeddingService"
    llm: "LLMService"

    # --- 业务服务 (lazy init) ---
    _indexer: Optional[object] = field(default=None, repr=False)
    _hybrid_search: Optional[object] = field(default=None, repr=False)
    _rag_pipeline: Optional[object] = field(default=None, repr=False)
    _query_rewriter: Optional[object] = field(default=None, repr=False)
    _reranker: Optional[object] = field(default=None, repr=False)
    _wiki_compiler: Optional[object] = field(default=None, repr=False)
    _wiki_repository: Optional[object] = field(default=None, repr=False)
    _wiki_write_service: Optional[object] = field(default=None, repr=False)
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

    # --- Phase 4 业务服务 (lazy init) ---
    _agent_memory: Optional[object] = field(default=None, repr=False)

    # --- M3 业务服务 (lazy init) ---
    _path_indexer: Optional[object] = field(default=None, repr=False)

    # --- W2: wiki-first 文件系统层编排 ---
    _knowledge_workflow: Optional[object] = field(default=None, repr=False)

    # --- 第二阶段:规模自适应路由(size-aware retrieval) ---
    _wiki_page_locator: Optional[object] = field(default=None, repr=False)
    _size_aware_router: Optional[object] = field(default=None, repr=False)

    # --- 第二阶段 W2:wiki parent-child 检索 ---
    _wiki_parent_retriever: Optional[object] = field(default=None, repr=False)

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
            self._hybrid_search = HybridSearcher(self.db, self.block_store, self.config)
            self._track_service("_hybrid_search")
        return self._hybrid_search

    @property
    def rag_pipeline(self):
        if self._rag_pipeline is None:
            from src.services.rag_pipeline import RAGService
            self._rag_pipeline = RAGService(deps={
                'db': self.db,
                'llm': self.llm,
                'query_rewriter': self.query_rewriter,
                'reranker': self.reranker,
                'hybrid_search': self.hybrid_search,
                'graph_backend': self.graph_backend,
                'size_aware_router': self.size_aware_router,
                'wiki_page_locator': self.wiki_page_locator,
                'wiki_parent_retriever': self.wiki_parent_retriever,
            })
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
            from src.services.rerankers import create_reranker
            self._reranker = create_reranker(config=self.config, llm=self.llm)
            self._track_service("_reranker")
        return self._reranker

    @property
    def wiki_compiler(self):
        if self._wiki_compiler is None:
            from src.services.wiki_compiler import WikiCompiler
            self._wiki_compiler = WikiCompiler()
            self._track_service("_wiki_compiler")
        return self._wiki_compiler

    @property
    def graph_builder(self):
        if self._graph_builder is None:
            from src.services.graph_builder import GraphBuilder
            self._graph_builder = GraphBuilder(graph_backend=self.graph_backend)
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
                self.config, self.db, self.block_store, self.embedding,
                graph_backend=self.graph_backend,
            )
            self._track_service("_file_graph_service")
        return self._file_graph_service

    # --- Phase 2 lazy services ---

    @property
    def unified_graph(self):
        if self._unified_graph is None:
            from src.services.unified_graph import UnifiedGraphService
            self._unified_graph = UnifiedGraphService(db=self.db, graph_backend=self.graph_backend)
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
            self._graph_traversal = GraphTraversalService(db=self.db, graph_backend=self.graph_backend)
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
            self._operation_log = OperationLogService(
                repo=self.operation_log_repo,
                knowledge_repo=self.knowledge_repo,
            )
            self._track_service("_operation_log")
        return self._operation_log

    @property
    def agent_memory(self):
        if self._agent_memory is None:
            from src.services.agent_memory import AgentMemoryService
            self._agent_memory = AgentMemoryService(
                repo=self.agent_memory_repo,
                db=self.db,
                llm=self.llm,
            )
            self._track_service("_agent_memory")
        return self._agent_memory

    @property
    def path_indexer(self):
        if self._path_indexer is None:
            from src.services.path_indexer import PathIndexService
            self._path_indexer = PathIndexService(
                db=self.db,
                config=self.config,
                indexed_file_repo=self.indexed_file_repo,
            )
            self._track_service("_path_indexer")
        return self._path_indexer

    @property
    def knowledge_workflow(self):
        if self._knowledge_workflow is None:
            from src.services.knowledge_workflow import KnowledgeWorkflowService
            self._knowledge_workflow = KnowledgeWorkflowService()
            self._track_service("_knowledge_workflow")
        return self._knowledge_workflow

    @property
    def wiki_repository(self):
        if self._wiki_repository is None:
            from pathlib import Path as _Path

            from src.services.wiki_repository import WikiRepository as _WikiRepo
            wiki_dir = self.config.get("knowledge_workflow.wiki_dir", "wiki")
            wiki_dir_path = _Path(wiki_dir)
            self._wiki_repository = _WikiRepo(
                wiki_dir=wiki_dir_path,
                registry_path=wiki_dir_path / "_meta" / "pages.json",
                redirects_path=wiki_dir_path / "_meta" / "redirects.json",
                outbox_path=_Path(self.config.get("storage.data_dir", "data")) / "wiki_projection_outbox.jsonl",
            )
            self._track_service("_wiki_repository")
        return self._wiki_repository

    @property
    def wiki_write_service(self):
        if self._wiki_write_service is None:
            from src.services.wiki_write_service import WikiWriteService
            self._wiki_write_service = WikiWriteService(
                wiki_compiler=self.wiki_compiler,
                knowledge_workflow=self.knowledge_workflow,
            )
            self._track_service("_wiki_write_service")
        return self._wiki_write_service

    @property
    def wiki_page_locator(self):
        if self._wiki_page_locator is None:
            from src.services.wiki_page_locator import WikiPageLocator
            self._wiki_page_locator = WikiPageLocator()
            self._track_service("_wiki_page_locator")
        return self._wiki_page_locator

    @property
    def size_aware_router(self):
        if self._size_aware_router is None:
            from src.services.size_aware_router import SizeAwareRouter
            self._size_aware_router = SizeAwareRouter(self.wiki_page_locator)
            self._track_service("_size_aware_router")
        return self._size_aware_router

    @property
    def wiki_parent_retriever(self):
        if self._wiki_parent_retriever is None:
            from src.services.wiki_parent_retrieval import WikiParentRetriever
            self._wiki_parent_retriever = WikiParentRetriever()
            self._track_service("_wiki_parent_retriever")
        return self._wiki_parent_retriever


def create_container(config_path: str | None = None) -> AppContainer:
    """创建并初始化应用容器

    按依赖拓扑顺序构建服务:
    1. Config — 加载配置
    2. Database — 创建 Database 实例（非类级别全局状态）
    3. VectorStore — 初始化向量存储
    4. EmbeddingService / LLMService — AI 服务
    """
    # 1. Config
    from src.utils.config import Config
    config = Config()
    config.load(config_path)
    logger.info("Config loaded")

    # 2. Database — 创建实例（同时设置 Database._instance 供向后兼容）
    from src.services.db import Database
    db: Database
    if Database._instance is not None:
        # 已有实例（如测试 fixture 已 connect），复用
        db = Database._instance
        logger.info("Database already connected, reusing existing instance")
    else:
        db_path = config.get_db_path()
        db = Database(str(db_path))
        logger.info("Database instance created: %s", db_path)

    # 3. VectorStore（注入 Database 实例）
    from src.services.vectorstore import VectorStore
    vectorstore = VectorStore(db=db)
    logger.info("VectorStore ready")

    from src.services.block_store import BlockStore
    block_store = BlockStore(db=db)
    logger.info("BlockStore ready")

    # 3.5 GraphBackend（插件式图后端）
    from src.services.graph_backend import create_graph_backend
    graph_backend = create_graph_backend(config, db=db)
    logger.info("GraphBackend ready: %s", graph_backend.name)

    # 4. Embedding / LLM
    from src.services.embedding import EmbeddingService
    from src.services.llm import LLMService
    embedding = EmbeddingService(config)
    llm = LLMService(config)
    logger.info("AI services initialized")

    # 启动期 API Key 存在性检查：缺失时告警一次（不阻断，保护纯检索用途）。
    # 用函数属性跨多次 create_container 去重，避免测试频繁调用时刷屏。
    if not getattr(create_container, "_key_check_done", False):
        setattr(create_container, "_key_check_done", True)
        _llm_key = config.get("llm.api_key", "")
        _emb_key = config.get("embedding.api_key", "") or _llm_key
        if not _llm_key:
            logger.warning(
                "启动检测：llm.api_key 未读取到，ask/RAG 生成、查询改写与 "
                "LLM 重排序将失败。配置路径：1) GUI 设置 → LLM；"
                "2) 环境变量 SHINEHE_LLM_API_KEY；3) keyring。"
                "Windows Service 需在服务账户下配置或注入系统环境变量。"
            )
        if not _emb_key:
            logger.warning(
                "启动检测：embedding.api_key 未读取到，向量索引与语义搜索"
                "将不可用（score_breakdown.vector 将为 null）。配置路径："
                "1) GUI 设置；2) 环境变量 SHINEHE_EMBEDDING_API_KEY；3) keyring。"
            )

    container = AppContainer(
        config=config,
        db=db,
        vectorstore=vectorstore,
        block_store=block_store,
        graph_backend=graph_backend,
        embedding=embedding,
        llm=llm,
    )

    # 初始化仓库层 — 全部注入 Database 实例
    from src.repositories.block_repo import BlockRepository
    from src.repositories.category_repo import CategoryRepository
    from src.repositories.conversation_repo import ConversationRepository
    from src.repositories.entity_ref_repo import EntityRefRepository
    from src.repositories.graph_repo import GraphRepository
    from src.repositories.job_repo import JobRepository
    from src.repositories.knowledge_repo import KnowledgeRepository
    from src.repositories.wiki_repo import WikiRepository
    container.knowledge_repo = KnowledgeRepository(db=db)
    container.conversation_repo = ConversationRepository(db=db)
    container.wiki_repo = WikiRepository(db=db)
    container.graph_repo = GraphRepository(db=db, graph_backend=graph_backend)
    container.block_repo = BlockRepository(db=db)
    container.entity_ref_repo = EntityRefRepository(db=db)
    container.category_repo = CategoryRepository(db=db)
    container.job_repo = JobRepository(db=db)

    # Phase 2 仓库
    from src.repositories.operation_log_repo import OperationLogRepository
    from src.repositories.property_schema_repo import PropertySchemaRepository
    from src.repositories.tag_relation_repo import TagRelationRepository
    container.tag_relation_repo = TagRelationRepository(db=db)
    container.property_schema_repo = PropertySchemaRepository(db=db)
    container.operation_log_repo = OperationLogRepository(db=db)

    # Phase 4 仓库
    from src.repositories.agent_memory_repo import AgentMemoryRepository
    container.agent_memory_repo = AgentMemoryRepository(db=db)

    # M3 仓库
    from src.repositories.indexed_file_repo import IndexedFileRepository
    container.indexed_file_repo = IndexedFileRepository(db=db)

    logger.info("Repositories initialized")

    # 设置模块级引用（替代旧的 Database._container 反向引用）
    global _active_container
    _active_container = container

    return container


def shutdown_container(container: AppContainer):
    """关闭容器，释放资源"""
    global _active_container
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
    finally:
        if _active_container is container:
            _active_container = None
