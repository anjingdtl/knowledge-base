"""KnowledgeTagRepository unit tests (WP4-T4)."""
from __future__ import annotations

import json

from src.repositories.knowledge_tag_repo import KnowledgeTagRepository
from src.services.db import Database
from tests.conftest import insert_test_knowledge


def test_list_untagged_and_update():
    kid = insert_test_knowledge("tag-doc", "body", tags=None)
    repo = KnowledgeTagRepository(Database._instance)
    rows = repo.list_untagged(limit=10, force=False)
    ids = [r["id"] for r in rows]
    assert kid in ids

    repo.update_tags(kid, ["alpha", "beta"])
    item = Database.get_knowledge(kid)
    assert json.loads(item["tags"]) == ["alpha", "beta"]

    rows2 = repo.list_untagged(limit=10, force=False)
    assert kid not in [r["id"] for r in rows2]

    rows3 = repo.list_untagged(limit=10, force=True)
    assert kid in [r["id"] for r in rows3]


def test_tagging_service_uses_repo_not_raw_sql():
    import inspect

    from src.application import tagging_service as mod

    src = inspect.getsource(mod)
    assert "get_conn()" not in src
    assert "SELECT id, title" not in src
    assert "KnowledgeTagRepository" in src
