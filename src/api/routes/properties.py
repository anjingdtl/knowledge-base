from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer

properties_router = APIRouter(prefix="/properties", tags=["properties"], dependencies=[Depends(_check_auth)])


class PropertySchemaReq(BaseModel):
    scope_type: str
    scope_id: str = ""
    property_name: str
    property_type: str
    required: int = 0
    default_value: object = None
    choices: list | None = None
    constraints: dict | None = None


@properties_router.post("/schemas")
def upsert_property_schema(data: PropertySchemaReq, container: AppContainer = Depends(get_container)):
    from src.models.property_schema import PropertySchema
    schema = container.property_schema.upsert(PropertySchema(**data.model_dump()))
    return schema.to_row()


@properties_router.get("/schemas")
def list_property_schemas(scope_type: str, scope_id: str = "", container: AppContainer = Depends(get_container)):
    return {"schemas": [schema.to_row() for schema in container.property_schema._repo.list_for_scope(scope_type, scope_id)]}


@properties_router.get("/effective/{block_id}")
def get_effective_properties(block_id: str, container: AppContainer = Depends(get_container)):
    return {"block_id": block_id, "properties": container.effective_properties.refresh_block(block_id)}
