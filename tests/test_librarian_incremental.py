from tests.conftest import insert_test_knowledge


def test_incremental_classification_removes_previous_uncategorized_assignment():
    from src.services.db import Database
    from src.services.librarian import LibrarianService

    service = LibrarianService()
    kid = insert_test_knowledge(
        title="Operations Playbook",
        content="Operational process notes",
        item_id="classified-kid",
    )
    service._save_with_schema([])

    cats = Database.get_all_categories()
    z_cat = next(cat for cat in cats if cat["name"].startswith("Z "))
    a1_cat = next(cat for cat in cats if cat["name"].startswith("A1 "))
    Database.assign_category(kid, z_cat["id"])

    service._save_incremental([
        {
            "code": "A",
            "name": "Strategy",
            "description": "",
            "item_ids": [],
            "children": [{
                "code": "A1",
                "name": "Planning",
                "description": "",
                "item_ids": [kid],
            }],
        }
    ])

    assigned_ids = {
        cat["id"] for cat in Database.get_categories_for_knowledge(kid)
    }
    assert a1_cat["id"] in assigned_ids
    assert z_cat["id"] not in assigned_ids
