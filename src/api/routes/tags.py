from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import get_container
from src.api.routes.auth import _check_auth
from src.core.container import AppContainer

tags_router = APIRouter(prefix="/tags", tags=["tags"], dependencies=[Depends(_check_auth)])


class TagRelationReq(BaseModel):
    parent_tag: str
    child_tag: str


class AutoTagReq(BaseModel):
    limit: int = 50
    force: bool = False


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


@tags_router.post("/auto-tag")
def auto_tag(data: AutoTagReq, container: AppContainer = Depends(get_container)):
    """智能补标：使用 tag_inference 多级管线（规则 + TF-IDF + LLM 兜底）为缺少标签的条目自动打标"""
    import json as _json

    from src.services.tag_inference import infer_tags

    db = container.db
    limit = min(data.limit, 500)

    # 查询缺少标签的条目
    if data.force:
        items = db.list_knowledge(limit=limit)
    else:
        rows = db.get_conn().execute(
            "SELECT id, title, content, source_path, tags FROM knowledge_items "
            "WHERE deleted_at IS NULL AND (tags IS NULL OR tags = '' OR tags = '[]') LIMIT ?",
            (limit,),
        ).fetchall()
        items = [dict(r) for r in rows]

    if not items:
        all_items = db.list_knowledge(limit=10000)
        tagged = sum(1 for it in all_items if it.get("tags") and it["tags"] not in ([], ""))
        total = len(all_items)
        return {"tagged_count": 0, "skipped_count": 0, "total": total, "coverage": tagged / total if total else 0, "message": "无需补标"}

    vocab = db.get_all_tags()
    tagged_count = 0
    skipped_count = 0
    errors = []
    tags_applied = set()

    for item in items:
        existing_tags = item.get("tags", [])
        if isinstance(existing_tags, str):
            try:
                existing_tags = _json.loads(existing_tags)
            except Exception:
                existing_tags = []
        if not isinstance(existing_tags, list):
            existing_tags = []

        try:
            results = infer_tags(item, vocab=vocab, use_llm=True)
        except Exception as e:
            errors.append(str(e))
            skipped_count += 1
            continue

        if results:
            new_tags = [r["tag"] for r in results if r.get("tag")]
            merged = list(dict.fromkeys(existing_tags + new_tags))[:5]
            try:
                db.update_knowledge_tags(item["id"], merged)
                tags_applied.update(new_tags)
                tagged_count += 1
            except Exception as e:
                errors.append(str(e))
                skipped_count += 1
        else:
            skipped_count += 1

    # 计算补标后覆盖率
    all_items = db.list_knowledge(limit=10000)
    after_tagged = sum(1 for it in all_items if it.get("tags") and it["tags"] not in ([], ""))
    total = len(all_items)

    return {
        "tagged_count": tagged_count,
        "skipped_count": skipped_count,
        "total": total,
        "coverage": after_tagged / total if total else 0,
        "tags_applied": sorted(tags_applied),
        "errors": errors[:10],
        "message": f"补标完成: {tagged_count} 条已标注, {skipped_count} 条跳过",
    }
