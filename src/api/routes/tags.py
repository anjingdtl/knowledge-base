from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer

tags_router = APIRouter(prefix="/tags", tags=["tags"], dependencies=[Depends(_check_auth)])


class TagRelationReq(BaseModel):
    parent_tag: str
    child_tag: str


@tags_router.post("/relations")
def create_tag_relation(data: TagRelationReq, container: AppContainer = Depends(get_container)):
    container.tag_hierarchy.add_relation(data.parent_tag, data.child_tag)
    return {"parent_tag": data.parent_tag, "child_tag": data.child_tag}


@tags_router.get("/hierarchy/{tag}")
def get_tag_hierarchy(tag: str, container: AppContainer = Depends(get_container)):
    return {
        "tag": tag,
        "ancestors": container.tag_hierarchy.ancestors(tag),
        "descendants": container.tag_hierarchy.descendants(tag),
    }
