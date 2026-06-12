import sys

from src import mcp_cli


def test_cli_exposes_selected_transport_to_write_policy(monkeypatch):
    import src.mcp_server as mcp_server

    calls = {}
    monkeypatch.setattr(
        sys,
        "argv",
        ["shinehe-mcp", "-t", "streamable-http", "--host", "127.0.0.1", "-p", "9010"],
    )
    monkeypatch.setattr(mcp_cli, "_patch_session_idle_timeout", lambda timeout: None)
    monkeypatch.setattr(
        mcp_server.mcp,
        "run",
        lambda **kwargs: calls.update(kwargs),
    )
    # 先登记一个受 monkeypatch 管理的值，确保 main() 的直接赋值在测试后恢复。
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")

    mcp_cli.main()

    assert calls["transport"] == "streamable-http"
    assert mcp_server.os.environ["MCP_TRANSPORT"] == "streamable-http"
