"""第6轮 BUG#12 回归测试：新增 delete_wiki_page / delete_memory MCP 工具。

此前 wiki 页面与 agent_memory 条目无法通过 MCP 删除（本轮测试遗留无法清理）。
两个 repo 方法（wiki_repo.delete_page / agent_memory_repo.delete）早已存在，
这里验证它们被正确暴露为 MCP 工具。
"""
from tests.conftest import insert_test_wiki_page


def test_delete_wiki_page_tool(setup_db):
    """delete_wiki_page 应删除 wiki 页面并返回 operation_id。"""
    from src.mcp_server import delete_wiki_page

    page_id = insert_test_wiki_page(
        title="待删除 Wiki 页面",
        content="这是一段将被删除的 wiki 内容。",
        status="draft",
    )

    result = delete_wiki_page(page_id=page_id)

    assert result["ok"] is True
    assert result["data"]["deleted"] is True
    assert result["data"]["page_id"] == page_id
    # 写工具应返回 operation_id
    assert result.get("operation_id")

    # 确认页面已从 DB 删除
    from src.services.db import Database
    assert Database.get_wiki_page(page_id) is None


def test_delete_wiki_page_not_found(setup_db):
    """删除不存在的 wiki 页面应返回 NOT_FOUND。"""
    from src.mcp_server import delete_wiki_page
    from src.utils.envelope import ErrorCode

    result = delete_wiki_page(page_id="nonexistent-page-id")
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.NOT_FOUND


def test_delete_memory_tool_by_id(setup_db):
    """delete_memory 按 item_id 删除记忆条目。"""
    from src.mcp_server import delete_memory, remember_fact

    # 先写入一条记忆
    remember_result = remember_fact(
        key="bug12_test_delete_by_id",
        value="用于测试删除的记忆条目",
        category="fact",
    )
    item_id = remember_result["data"]["id"]

    result = delete_memory(item_id=item_id)

    assert result["ok"] is True
    assert result["data"]["deleted"] is True
    assert result.get("operation_id")

    # 确认已删除
    from src.repositories.agent_memory_repo import AgentMemoryRepository
    repo = AgentMemoryRepository()
    assert repo.get_by_id(item_id) is None


def test_delete_memory_tool_by_key(setup_db):
    """delete_memory 按 key 删除记忆条目。"""
    from src.mcp_server import delete_memory, remember_fact

    remember_fact(
        key="bug12_test_delete_by_key",
        value="用于测试按 key 删除的记忆条目",
        category="decision",
    )

    result = delete_memory(key="bug12_test_delete_by_key")

    assert result["ok"] is True
    assert result["data"]["deleted"] is True

    from src.repositories.agent_memory_repo import AgentMemoryRepository
    repo = AgentMemoryRepository()
    assert repo.get_by_key("bug12_test_delete_by_key") is None


def test_delete_memory_requires_param(setup_db):
    """delete_memory 不传 item_id 和 key 应返回 VALIDATION_ERROR。"""
    from src.mcp_server import delete_memory
    from src.utils.envelope import ErrorCode

    result = delete_memory()
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.VALIDATION_ERROR


def test_delete_memory_not_found(setup_db):
    """删除不存在的记忆应返回 NOT_FOUND。"""
    from src.mcp_server import delete_memory
    from src.utils.envelope import ErrorCode

    result = delete_memory(item_id="nonexistent-memory-id")
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.NOT_FOUND


def test_delete_tools_registered_in_metadata():
    """两个新工具应在 _TOOL_METADATA 中且标记为 destructive。"""
    from src.mcp_server import _TOOL_METADATA

    assert "delete_wiki_page" in _TOOL_METADATA
    assert "delete_memory" in _TOOL_METADATA
    assert _TOOL_METADATA["delete_wiki_page"]["side_effect"] == "destructive"
    assert _TOOL_METADATA["delete_memory"]["side_effect"] == "destructive"
    # destructive 工具必须 require_confirmation
    assert _TOOL_METADATA["delete_wiki_page"]["requires_confirmation"] is True
    assert _TOOL_METADATA["delete_memory"]["requires_confirmation"] is True
