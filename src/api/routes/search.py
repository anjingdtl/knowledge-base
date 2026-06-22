from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer

query_router = APIRouter(prefix="/query", tags=["query"], dependencies=[Depends(_check_auth)])


class QueryDSLReq(BaseModel):
    filter: dict
    limit: int = 100
    offset: int = 0
    sort: dict | list[dict] | None = None
    include_blocks: bool = False


@query_router.post("")
def execute_structured_query(data: QueryDSLReq, container: AppContainer = Depends(get_container)):
    from src.models.query_dsl import QuerySpec
    from src.services.query_executor import QueryExecutor

    dsl = data.model_dump(exclude_none=True)
    spec = QuerySpec.from_json(dsl)
    executor = QueryExecutor(db=container.db)
    results = executor.execute(spec)
    return {"results": results, "total": len(results)}


@query_router.post("/explain")
def explain_structured_query(data: QueryDSLReq, container: AppContainer = Depends(get_container)):
    from src.models.query_dsl import QuerySpec
    from src.services.query_explainer import QueryExplainer

    spec = QuerySpec.from_json(data.model_dump(exclude_none=True))
    explainer = QueryExplainer()
    return explainer.explain(spec)
