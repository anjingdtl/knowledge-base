"""数据库操作测试"""

from src.models.knowledge import KnowledgeItem
from src.services.db import Database


class TestKnowledgeCRUD:
    def test_insert_and_get(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        got = Database.get_knowledge(sample_item.id)
        assert got is not None
        assert got["title"] == "测试知识"
        assert got["content"] == sample_item.content

    def test_list_knowledge(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        items = Database.list_knowledge()
        assert len(items) >= 1
        assert items[0]["title"] == "测试知识"

    def test_update_knowledge(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        Database.update_knowledge(sample_item.id, title="更新后的标题")
        got = Database.get_knowledge(sample_item.id)
        assert got["title"] == "更新后的标题"
        assert got["version"] == 2

    def test_delete_knowledge(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        Database.delete_knowledge(sample_item.id)
        got = Database.get_knowledge(sample_item.id)
        assert got is None

    def test_search_knowledge(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        results = Database.search_knowledge("测试")
        assert len(results) >= 1

    def test_count_knowledge(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        assert Database.count_knowledge() >= 1

    def test_tag_filter(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        items = Database.list_knowledge(tag="测试")
        assert len(items) >= 1

    def test_get_all_tags(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        tags = Database.get_all_tags()
        assert "测试" in tags

    def test_sort_by_title(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        item2 = KnowledgeItem(title="AAA排序", content="c")
        Database.insert_knowledge(item2.to_row())
        items = Database.list_knowledge(sort_by="title", sort_order="ASC")
        assert items[0]["title"] == "AAA排序"


class TestVersionControl:
    def test_version_saved_on_update(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        Database.update_knowledge(sample_item.id, content="新内容 v2")
        versions = Database.list_versions(sample_item.id)
        assert len(versions) == 1
        assert versions[0]["content"] == sample_item.content

    def test_multiple_versions(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        Database.update_knowledge(sample_item.id, content="v2")
        Database.update_knowledge(sample_item.id, content="v3")
        versions = Database.list_versions(sample_item.id)
        assert len(versions) == 2

    def test_restore_version(self, sample_item):
        Database.insert_knowledge(sample_item.to_row())
        Database.update_knowledge(sample_item.id, content="v2")
        Database.restore_version(sample_item.id, 1)
        got = Database.get_knowledge(sample_item.id)
        assert got["content"] == sample_item.content
        assert got["version"] == 3
