"""Tests for classification schema compatibility."""

from src.services.db import Database
from src.services.librarian import LibrarianService, iter_classification_schema


def test_iter_classification_schema_normalizes_list_schema():
    items = list(iter_classification_schema())

    assert items[0]["code"] == "A"
    assert items[0]["name"] == "战略与管理"
    assert items[0]["subcategories"][0]["code"] == "A1"
    assert items[0]["subcategories"][0]["name"] == "企业战略规划"


def test_parse_response_accepts_subcategory_from_list_schema():
    service = LibrarianService()
    response = '[{"code": "A1", "ids": [1], "is_new": false}]'
    batch = [{"id": "kid-1", "title": "战略规划制度", "content": "战略规划", "file_type": "txt"}]

    parsed = service._parse_response(response, batch, db_cats=[], valid_codes={"A", "A1", "Z"})

    assert parsed == [
        {
            "code": "A",
            "name": "战略与管理",
            "description": "企业战略规划、经营分析、绩效考核、管理制度",
            "item_ids": [],
            "children": [
                {
                    "code": "A1",
                    "name": "企业战略规划",
                    "description": "",
                    "item_ids": ["kid-1"],
                }
            ],
        }
    ]


def test_save_with_schema_creates_categories_from_list_schema():
    service = LibrarianService()

    service._save_with_schema([
        {
            "code": "A",
            "name": "战略与管理",
            "description": "企业战略规划、经营分析、绩效考核、管理制度",
            "item_ids": [],
            "children": [],
        }
    ])

    names = {cat["name"] for cat in Database.get_all_categories()}
    assert "A 战略与管理" in names
    assert "A1 企业战略规划" in names
    assert "Z 未分类" in names
