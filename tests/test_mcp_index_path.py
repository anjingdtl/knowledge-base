"""MCP index_path security contract tests."""

from src.models.indexing import IndexResult
from src.utils.envelope import ErrorCode


def test_index_path_rejects_directory_outside_allowed_roots(monkeypatch, tmp_path):
    import src.mcp_server as mcp_server
    from src.utils import paths

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    class PathIndexer:
        def index_path(self, *args, **kwargs):
            raise AssertionError("unauthorized path reached PathIndexService")

    class Container:
        path_indexer = PathIndexer()

    monkeypatch.setattr(mcp_server, "_check_write_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp_server, "_get_container", lambda: Container())
    monkeypatch.setattr(mcp_server.Config, "get", lambda key, default=None: [])
    monkeypatch.setattr(paths, "get_data_dir", lambda: allowed)
    monkeypatch.setattr(paths, "get_project_root", lambda: allowed)
    monkeypatch.setattr(mcp_server.os.path, "expanduser", lambda value: str(allowed))
    monkeypatch.delenv("SHINEHE_HOME", raising=False)

    result = mcp_server.index_path(str(outside))

    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.PERMISSION_DENIED


def test_index_path_accepts_directory_inside_allowed_roots(monkeypatch, tmp_path):
    import src.mcp_server as mcp_server
    from src.utils import paths

    allowed = tmp_path / "allowed"
    allowed.mkdir()

    class PathIndexer:
        def index_path(self, path, **kwargs):
            return IndexResult(created=1)

    class Container:
        path_indexer = PathIndexer()

    monkeypatch.setattr(mcp_server, "_check_write_policy", lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp_server, "_get_container", lambda: Container())
    monkeypatch.setattr(mcp_server.Config, "get", lambda key, default=None: [])
    monkeypatch.setattr(paths, "get_data_dir", lambda: allowed)
    monkeypatch.setattr(paths, "get_project_root", lambda: allowed)
    monkeypatch.setattr(mcp_server.os.path, "expanduser", lambda value: str(allowed))
    monkeypatch.delenv("SHINEHE_HOME", raising=False)

    result = mcp_server.index_path(str(allowed))

    assert result["ok"] is True
    assert result["data"]["created"] == 1
