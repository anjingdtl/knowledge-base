"""ShineHeKnowledge MCP Server CLI 入口"""
import argparse
import os

# 默认 Session 空闲超时（秒）。None = 永不过期（SDK 默认）。
# 设为 86400（24h）防止客户端长时间空闲后 "Session not found"。
SESSION_IDLE_TIMEOUT = 86400


def _patch_session_idle_timeout(timeout: float | None):
    """Monkey-patch FastMCP 的 StreamableHTTPSessionManager 构造，注入 session_idle_timeout。

    FastMCP v3.2.x 未暴露此参数，需要 patch create_streamable_http_app
    中 StreamableHTTPSessionManager 的构造调用。
    """
    from fastmcp.server import http as _http_mod

    _orig_create = _http_mod.create_streamable_http_app

    def _patched_create(*args, **kwargs):
        app = _orig_create(*args, **kwargs)
        # app 的 lifespan 中会创建 StreamableHTTPSessionManager，
        # 但 lifespan 是闭包，我们无法直接修改参数。
        # 改为 patch StreamableHTTPSessionManager.__init__ 临时注入默认值。
        return app

    # 更精准：直接 patch SessionManager 的 __init__，用闭包捕获 timeout
    _OrigManager = _http_mod.StreamableHTTPSessionManager
    _orig_init = _OrigManager.__init__

    def _patched_init(self, *a, **kw):
        # 仅在调用方未显式传入时注入
        if "session_idle_timeout" not in kw:
            kw["session_idle_timeout"] = timeout
        _orig_init(self, *a, **kw)

    _OrigManager.__init__ = _patched_init  # type: ignore[method-assign]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="shinehe-mcp",
        description="ShineHeKnowledge MCP Server - 本地知识库 MCP 服务",
    )
    parser.add_argument(
        "--transport", "-t",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="传输模式（默认: stdio）",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP 模式绑定地址（默认: 127.0.0.1）",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=9000,
        help="HTTP 模式端口（默认: 9000）",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="配置文件路径（默认: 自动检测）",
    )
    args = parser.parse_args(argv)

    if args.config:
        config_dir = os.path.dirname(os.path.abspath(args.config))
        os.environ["SHINEHE_HOME"] = config_dir

    # 写操作安全策略需要知道当前实际传输模式。
    os.environ["MCP_TRANSPORT"] = args.transport

    # HTTP 模式下 patch Session TTL，防止空闲断连
    if args.transport != "stdio":
        _patch_session_idle_timeout(SESSION_IDLE_TIMEOUT)

    from src.mcp_server import mcp

    kwargs = {}
    if args.transport != "stdio":
        kwargs["host"] = args.host
        kwargs["port"] = args.port

    mcp.run(transport=args.transport, **kwargs)


if __name__ == "__main__":
    main()
