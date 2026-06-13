"""ShineHeKnowledge 统一 CLI 入口

子命令:
  shinehe init    — 初始化项目配置
  shinehe index   — 索引文档
  shinehe watch   — 监听文件变更
  shinehe doctor  — 健康检查
  shinehe mcp     — 启动 MCP 服务（委托给 mcp_cli）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.version import APP_NAME, VERSION


def _handle_init(args: argparse.Namespace) -> int:
    """处理 init 子命令"""
    from src.services.project_setup import ProjectSetupService

    service = ProjectSetupService()
    request = {
        "local": args.local,
        "path": args.path,
        "provider": args.provider,
        "clients": [c.strip() for c in args.client.split(",")] if args.client else [],
        "force": args.force,
    }
    config = service.build_config(request)
    target = Path(args.path) if args.path else None
    config_path = service.write_config(target, config, force=args.force)
    print(f"[OK] 配置已写入: {config_path}")

    if request["clients"]:
        server_config = service.build_server_config(config_path)
        service.configure_clients(request["clients"], server_config)

    return 0


def _handle_index(args: argparse.Namespace) -> int:
    """处理 index 子命令"""
    target = Path(args.path).resolve()
    if not target.exists():
        print(f"[ERROR] 路径不存在: {target}", file=sys.stderr)
        return 1

    print(f"索引路径: {target}")
    if args.recursive:
        print("  模式: 递归")
    if args.dry_run:
        print("  模式: 预览（dry-run）")
    if args.force:
        print("  模式: 强制重建")

    from src.services.path_indexer import PathIndexService
    indexer = PathIndexService()
    result = indexer.index_path(
        target,
        recursive=args.recursive,
        dry_run=args.dry_run,
        force=args.force,
    )

    if result.mode == "async":
        print(f"[ASYNC] 已提交异步任务 (job_id={result.job_id})，请稍后查看进度。")
    else:
        print(f"索引完成: +{result.created} ~{result.updated} -{result.deleted} (跳过 {result.skipped})")
        if result.failed:
            print(f"失败: {len(result.failed)} 个文件")
            for f in result.failed:
                print(f"  [ERROR] {f['path']}: {f['error']}")

    return 0


def _handle_watch(args: argparse.Namespace) -> int:
    """处理 watch 子命令"""
    target = Path(args.path).resolve()
    if not target.exists():
        print(f"[ERROR] 路径不存在: {target}", file=sys.stderr)
        return 1

    print(f"监听路径: {target}")
    if args.recursive:
        print("  模式: 递归")

    from src.services.file_watcher import FileWatcher
    from src.services.index_scheduler import IndexScheduler
    from src.services.path_indexer import PathIndexService

    indexer = PathIndexService()
    scheduler = IndexScheduler(path_indexer=indexer, debounce_ms=500)

    try:
        watcher = FileWatcher(
            scheduler=scheduler, root=target, recursive=args.recursive
        )
        watcher.start()
        print("文件监听已启动，按 Ctrl+C 停止...")

        import signal
        import time

        shutdown_requested = False

        def _signal_handler(signum, frame):
            nonlocal shutdown_requested
            shutdown_requested = True

        signal.signal(signal.SIGINT, _signal_handler)

        while not shutdown_requested:
            time.sleep(1)
            # 每次循环 flush 待处理事件
            result = scheduler.flush()
            if result.created or result.updated or result.deleted:
                print(
                    f"  变更: +{result.created} ~{result.updated} -{result.deleted}"
                )
            if result.failed:
                for f in result.failed:
                    print(f"  [ERROR] {f['path']}: {f['error']}", file=sys.stderr)

    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    finally:
        scheduler.shutdown()
        try:
            watcher.stop()
        except Exception:
            pass
        print("\n文件监听已停止")

    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    """处理 doctor 子命令"""
    from src.services.doctor import DoctorService

    service = DoctorService()
    results = service.run_all_checks(config_path=args.config)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    warn_count = sum(1 for r in results if r["status"] == "warn")
    fail_count = sum(1 for r in results if r["status"] == "fail")

    for r in results:
        icon = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}.get(r["status"], "[?]")
        print(f"  {icon} {r['name']}: {r['message']}")

    print(f"\n结果: {ok_count} 正常, {warn_count} 警告, {fail_count} 失败")

    if fail_count > 0:
        return 1
    if warn_count > 0:
        return 2
    return 0


def _handle_mcp(args: argparse.Namespace) -> int:
    """处理 mcp 子命令：委托给 mcp_cli"""
    from src.mcp_cli import main as mcp_main

    argv: list[str] = []
    if args.transport != "stdio":
        argv.extend(["--transport", args.transport])
    if args.host != "127.0.0.1":
        argv.extend(["--host", args.host])
    if args.port != 9000:
        argv.extend(["--port", str(args.port)])

    mcp_main(argv=argv)
    return 0


def main(argv: list[str] | None = None) -> None:
    """ShineHeKnowledge CLI 主入口"""
    parser = argparse.ArgumentParser(
        prog="shinehe",
        description=f"{APP_NAME} CLI v{VERSION} - 本地知识库管理工具",
    )
    parser.add_argument(
        "--version", action="version", version=f"{APP_NAME} {VERSION}",
    )

    subparsers = parser.add_subparsers(dest="command", help="可用子命令")

    # --- init ---
    init_parser = subparsers.add_parser(
        "init", help="初始化项目配置",
        description="初始化 ShineHeKnowledge 项目，生成配置文件并可选配置 MCP 客户端。",
    )
    init_parser.add_argument(
        "--local", action="store_true",
        help="本地模式：使用 Ollama 预设，禁用远程服务",
    )
    init_parser.add_argument(
        "--path", default=None,
        help="配置文件目标目录（默认: ~/.shinehe/ 或 SHINEHE_HOME）",
    )
    init_parser.add_argument(
        "--client", default=None,
        help="要配置的 MCP 客户端，逗号分隔 (claude-code,cursor,cline)",
    )
    init_parser.add_argument(
        "--provider", default="siliconflow",
        help="AI 服务商预设名称（默认: siliconflow）",
    )
    init_parser.add_argument(
        "--force", action="store_true",
        help="覆盖已有配置文件",
    )

    # --- index ---
    index_parser = subparsers.add_parser(
        "index", help="索引文档到知识库",
        description="将指定路径下的文档索引到知识库中。",
    )
    index_parser.add_argument("path", help="要索引的文件或目录路径")
    index_parser.add_argument(
        "--recursive", "-r", action="store_true",
        help="递归扫描子目录",
    )
    index_parser.add_argument(
        "--dry-run", action="store_true",
        help="预览模式，不实际写入数据",
    )
    index_parser.add_argument(
        "--force", action="store_true",
        help="强制重新索引已有文档",
    )

    # --- watch ---
    watch_parser = subparsers.add_parser(
        "watch", help="监听文件变更并自动索引",
        description="持续监听指定路径的文件变更，自动增量索引。",
    )
    watch_parser.add_argument("path", help="要监听的文件或目录路径")
    watch_parser.add_argument(
        "--recursive", "-r", action="store_true",
        help="递归监听子目录",
    )

    # --- doctor ---
    doctor_parser = subparsers.add_parser(
        "doctor", help="健康检查",
        description="检查系统环境、配置完整性、依赖可用性等。",
    )
    doctor_parser.add_argument(
        "--config", default=None,
        help="自定义配置文件路径",
    )

    # --- mcp ---
    mcp_parser = subparsers.add_parser(
        "mcp", help="启动 MCP 服务",
        description="启动 ShineHeKnowledge MCP Server（委托给 shinehe-mcp）。",
    )
    mcp_parser.add_argument(
        "--transport", default="stdio", choices=["stdio", "streamable-http", "sse"],
        help="传输模式（默认: stdio）",
    )
    mcp_parser.add_argument(
        "--host", default="127.0.0.1",
        help="HTTP 模式绑定地址（默认: 127.0.0.1）",
    )
    mcp_parser.add_argument(
        "--port", type=int, default=9000,
        help="HTTP 模式端口（默认: 9000）",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "init": _handle_init,
        "index": _handle_index,
        "watch": _handle_watch,
        "doctor": _handle_doctor,
        "mcp": _handle_mcp,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    exit_code = handler(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
