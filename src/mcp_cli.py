"""ShineHeKnowledge MCP Server CLI 入口"""
import argparse
import os


def main():
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
    args = parser.parse_args()

    if args.config:
        config_dir = os.path.dirname(os.path.abspath(args.config))
        os.environ["SHINEHE_HOME"] = config_dir

    from src.mcp_server import mcp

    kwargs = {}
    if args.transport != "stdio":
        kwargs["host"] = args.host
        kwargs["port"] = args.port

    mcp.run(transport=args.transport, **kwargs)


if __name__ == "__main__":
    main()
