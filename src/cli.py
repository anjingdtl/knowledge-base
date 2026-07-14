"""ShineHeKnowledge 统一 CLI 入口

子命令:
  shinehe init    — 初始化项目配置
  shinehe index   — 索引文档
  shinehe watch   — 监听文件变更
  shinehe doctor  — 健康检查
  shinehe mcp     — 启动 MCP 服务（委托给 mcp_cli）
  shinehe db      — 数据库迁移治理（status/backup/migrate/stamp/verify）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.version import APP_NAME, VERSION


def _handle_init(args: argparse.Namespace) -> int:
    """处理 init 子命令"""
    from src.services.project_setup import ProjectSetupService
    from src.utils.knowledge_mode import (
        MODE_EVIDENCE_ONLY,
        MODE_VERIFIED,
        InvalidKnowledgeModeError,
        allows_authoring,
        allows_wiki_read,
        resolve_knowledge_mode,
    )

    try:
        mode = resolve_knowledge_mode(getattr(args, "mode", None) or MODE_VERIFIED)
    except InvalidKnowledgeModeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    service = ProjectSetupService()
    request = {
        "local": args.local,
        "path": args.path,
        "provider": args.provider,
        "clients": [c.strip() for c in args.client.split(",")] if args.client else [],
        "force": args.force,
        "mode": mode,
    }
    config = service.build_config(request)
    target = Path(args.path) if args.path else None
    config_path = service.write_config(target, config, force=args.force)
    print(f"[OK] 配置已写入: {config_path}")
    print(f"[OK] 知识模式: {mode}")
    print("[OK] 原始文档检索: enabled")
    if allows_wiki_read(mode):
        print("[OK] 已验证 Wiki 读取: enabled")
    else:
        print("[OK] 已验证 Wiki 读取: disabled")

    # Authoring 目录契约仅在 authoring 模式创建（Spec §10 / §12.2）
    project_dir = Path(args.path) if args.path else Path.cwd()
    if allows_authoring(mode):
        layout = service.write_wiki_first_layout(project_dir)
        print(
            f"[OK] Wiki Authoring 目录已就绪: {project_dir} "
            f"({len(layout)} 个目录 + AGENTS.md)"
        )
        print("[OK] Wiki Authoring: enabled")
    else:
        print("[OK] Wiki Authoring: disabled")
        if mode == MODE_VERIFIED:
            print(
                "[INFO] 当前无可用 Canonical Claim 时将自动使用原始文档检索"
                "（Serving Gate 见后续阶段）"
            )
        if mode == MODE_EVIDENCE_ONLY:
            print("[INFO] evidence_only：仅原始文档检索，适合降级与对照评测")

    wiki_cfg = config.get("wiki") or {}
    if wiki_cfg.get("authoring_enabled"):
        print("[OK] Maintenance Center: supervised (authoring + protective)")
    elif mode == MODE_VERIFIED:
        print("[OK] Maintenance Center: supervised (protective actions only)")
    else:
        print("[OK] Maintenance Center: observe (evidence_only)")

    if request["clients"]:
        server_config = service.build_server_config(config_path)
        service.configure_clients(request["clients"], server_config)
        print(f"[OK] MCP 客户端已配置: {', '.join(request['clients'])}")

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
    import json

    from src.services.doctor import DoctorService

    service = DoctorService()
    if getattr(args, "explain_config", False):
        try:
            print(json.dumps(service.explain_config(args.config), ensure_ascii=False, indent=2))
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] 配置解析失败: {e}", file=sys.stderr)
            return 1
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


def _handle_config(args: argparse.Namespace) -> int:
    """Run the explicit, backup-first Verified Hybrid configuration migration."""
    import json

    if getattr(args, "config_command", None) != "migrate-verified-hybrid":
        print("用法: shinehe config migrate-verified-hybrid --dry-run")
        return 0
    from src.services.verified_hybrid_config_migrator import VerifiedHybridConfigMigrator

    try:
        migrator = VerifiedHybridConfigMigrator(args.config)
        if getattr(args, "rollback", None):
            report = migrator.rollback(args.rollback)
        elif getattr(args, "apply", False):
            report = migrator.apply(target_mode=args.target_mode)
        else:
            report = migrator.dry_run(target_mode=args.target_mode)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] 配置迁移失败: {e}", file=sys.stderr)
        return 1
    prefix = "[DRY-RUN]" if report.dry_run else "[APPLY]"
    print(f"{prefix} {json.dumps(report.to_dict(), ensure_ascii=False, indent=2)}")
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


def _handle_wiki(args: argparse.Namespace) -> int:
    """处理 wiki 子命令组:lint / save-answer / ingest-source / migrate-v2 / validate。"""
    cmd = getattr(args, "wiki_command", None)
    if cmd is None:
        print("用法: shinehe wiki <lint|save-answer|ingest-source|migrate-v2|validate|claims>")
        return 0

    if cmd == "lint":
        from src.utils.config import Config
        source = getattr(args, "source", "auto")
        from src.utils.knowledge_mode import allows_authoring, get_configured_knowledge_mode

        mode = get_configured_knowledge_mode()
        # FS wiki eval 面向 Authoring 布局；wiki_first 兼容映射到 authoring
        use_fs = source == "fs" or (source == "auto" and allows_authoring(mode))

        if use_fs:
            from src.services.wiki_fs_lint import WikiFsLint
            wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))
            report = WikiFsLint(wiki_dir=wiki_dir).run()
            print(f"[lint] 数据源: 文件系统 {wiki_dir}")
        else:
            from src.services.wiki_lint import WikiLint
            if not Config.get("wiki.enabled", False):
                print("[WARN] wiki 未启用(配置 wiki.enabled=true)")
            report = WikiLint().run()
            print("[lint] 数据源: SQLite wiki_pages")

        for f in report["findings"][:20]:
            print(f"  [{f['severity'].upper()}] {f['category']}: {f['page_title']} — {f['message']}")
        extra = f" (另有 {len(report['findings']) - 20} 条)" if len(report["findings"]) > 20 else ""
        print(f"\n结果: {len(report['findings'])} 个问题{extra}, 健康分 {report['score']:.2f}, 共 {report['total_pages']} 页")
        return 1 if report["findings"] else 0

    if cmd == "save-answer":
        from datetime import datetime

        from src.services.knowledge_workflow import KnowledgeWorkflowService
        result = KnowledgeWorkflowService().save_query(
            question=args.question, answer=args.answer,
            source_ids=[], confidence=1.0,
            page_type="syntheses", save_mode="manual",
            timestamp=datetime.now().isoformat(),
        )
        print(f"[OK] 保存: {result.get('path', result.get('status'))}")
        return 0

    if cmd == "ingest-source":
        target = Path(args.path).resolve()
        if not target.exists():
            print(f"[ERROR] 路径不存在: {target}", file=sys.stderr)
            return 1
        from src.core.container import create_container
        container = create_container()
        kid = container.path_indexer._ingest_file(target)
        print(f"[OK] ingest 完成: {kid}")
        return 0

    if cmd == "migrate-v2":
        from src.core.container import create_container

        container = create_container()
        migrator = container.wiki_v2_migrator
        if getattr(args, "rollback", None):
            report = migrator.rollback(args.rollback)
            print(f"[ROLLBACK] backup={report.backup_path} errors={report.errors}")
            if report.suggestion:
                print(f"  {report.suggestion}")
            return 1 if report.errors else 0
        if getattr(args, "apply", False):
            report = migrator.apply()
            print(
                f"[APPLY] writes={report.writes} pages={report.pages_to_create} "
                f"claims={report.claims_to_create} conflicts={report.conflicts} "
                f"backup={report.backup_path}"
            )
        else:
            report = migrator.dry_run()
            print(
                f"[DRY-RUN] a={report.a_page_count} b={report.b_page_count} "
                f"create_pages={report.pages_to_create} claims={report.claims_to_create} "
                f"conflicts={report.conflicts} already_canonical={report.already_canonical} "
                f"untraceable={report.untraceable_facts}"
            )
        if report.errors:
            for e in report.errors:
                print(f"  [ERROR] {e}", file=sys.stderr)
        if report.warnings:
            for w in report.warnings[:10]:
                print(f"  [WARN] {w}")
        if report.suggestion:
            print(f"  suggestion: {report.suggestion}")
        return 1 if report.errors else 0

    if cmd == "validate":
        from src.core.container import create_container
        from src.services.wiki_validator import WikiValidator

        container = create_container()
        wiki_dir = Path(container.config.get("knowledge_workflow.wiki_dir", "wiki"))
        validator = WikiValidator(wiki_dir=wiki_dir)
        findings = validator.validate_directory()
        # 扩展:扫描 claims provenance
        if hasattr(validator, "validate_canonical_store"):
            findings.extend(validator.validate_canonical_store(container.wiki_repository))
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        for f in findings[:30]:
            print(f"  [{f.severity.upper()}] {f.category}: {f.object_id} — {f.message}")
        extra = f" (另有 {len(findings) - 30} 条)" if len(findings) > 30 else ""
        print(f"\n结果: {len(errors)} errors, {len(warnings)} warnings{extra}")
        strict = getattr(args, "strict", False)
        if strict and errors:
            return 1
        return 0

    if cmd == "serving-validation-migration":
        from src.core.container import create_container
        from src.services.wiki_serving_validation_migrator import WikiServingValidationMigrator

        if getattr(args, "apply", False):
            print("serving validation apply is gated until Phase 8; use --dry-run")
            return 2
        validation_report = WikiServingValidationMigrator(create_container().wiki_repository).dry_run()
        print(json.dumps(validation_report.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if cmd == "claims":
        sub = getattr(args, "claims_command", None)
        from src.core.container import create_container

        container = create_container()
        if sub == "list":
            claims = container.wiki_repository.list_claims()
            status_filter = getattr(args, "status", None)
            for c in claims:
                if status_filter and c.status.value != status_filter:
                    continue
                print(f"  {c.claim_id} [{c.status.value}] {c.statement[:80]}")
            print(f"\n共 {len(claims)} 条 claim")
            return 0
        if sub == "show":
            claim = container.wiki_repository.get_claim(args.claim_id)
            if not claim:
                print(f"[ERROR] claim 不存在: {args.claim_id}", file=sys.stderr)
                return 1
            print(f"id: {claim.claim_id}")
            print(f"status: {claim.status.value}")
            print(f"statement: {claim.statement}")
            print(f"evidence: {len(claim.evidence)}")
            for ev in claim.evidence:
                print(
                    f"  - {ev.evidence_id} {ev.stance.value} kid={ev.knowledge_id} "
                    f"block={ev.block_id} stale={ev.stale}"
                )
            return 0
        if sub == "review":
            result = container.wiki_feedback_service.apply(
                args.claim_id,
                args.action,
                correction=getattr(args, "correction", None),
                operator=getattr(args, "operator", "cli"),
                note=getattr(args, "note", "") or "",
            )
            if result.errors:
                for e in result.errors:
                    print(f"[ERROR] {e}", file=sys.stderr)
                return 1
            print(
                f"[OK] {result.action}: {result.claim_id} "
                f"{result.before_status} → {result.after_status} "
                f"op={result.op_log_id}"
            )
            return 0
        print("用法: shinehe wiki claims <list|show|review>", file=sys.stderr)
        return 1

    print(f"[ERROR] 未知 wiki 子命令: {cmd}", file=sys.stderr)
    return 1


def _handle_migrate(args: argparse.Namespace) -> int:
    """处理 migrate 子命令:legacy -> wiki-first。"""
    # 必须先加载配置:plan 分支不走 create_container,否则 _ensure_db 会用默认
    # data_dir/db_name(误读 ./data/kb.db),与用户实际配置的库不一致。
    from src.utils.config import Config
    Config.load()
    from src.services.migrator import MigrationService
    svc = MigrationService()
    if not args.apply:
        plan = svc.plan()
        print(f"[PLAN] knowledge: {plan['knowledge_count']}, 可导出: {plan['exportable']}")
        for a in plan["actions"][:20]:
            mark = "[OK]" if a["action"] == "export" else "[SKIP]"
            print(f"  {mark} {a['title'][:40]} -> {a['source_path']}")
        if len(plan["actions"]) > 20:
            print(f"  ...(另有 {len(plan['actions']) - 20} 条)")
        print("\n使用 --apply 执行迁移(将备份 data/、导出源到 raw/、重编译 wiki)")
        return 0
    # apply 需要显式注入 knowledge_workflow 以驱动 wiki 重编译。
    from src.core.container import create_container
    container = create_container()
    result = svc.apply(
        backup=not args.no_backup,
        knowledge_workflow=container.knowledge_workflow,
    )
    print(f"[OK] 导出 {result['exported']} 源, 跳过 {result['skipped_missing']}, "
          f"重编译 {result['recompiled']}, 备份={'是' if result['backup_created'] else '否'}")
    return 0


def _handle_rebuild(args: argparse.Namespace) -> int:
    """处理 rebuild 子命令(Phase 5):来源失效重建。"""
    from src.core.container import create_container

    container = create_container()
    if args.dry_run:
        plan = container.wiki_rebuild_service.plan_rebuild(args.knowledge_id, event=args.event)
        print(f"[PLAN] knowledge_id={args.knowledge_id} event={args.event}")
        print(f"  affected_evidence={len(plan.affected_evidence)} "
              f"claims={len(plan.affected_claims)} pages={len(plan.affected_pages)}")
        print(f"  truncated={plan.truncated} cycle_warnings={len(plan.cycle_warnings)}")
        return 0
    result = container.wiki_rebuild_service.rebuild(args.knowledge_id, event=args.event)
    status = "CANCELLED" if result.cancelled else ("OK" if result.committed else "FAILED")
    print(f"[{status}] knowledge_id={result.knowledge_id} event={result.event} "
          f"committed={result.committed} cancelled={result.cancelled}")
    for w in result.warnings:
        print(f"  warn: {w}")
    print(f"  stats: {result.plan.stats}")
    return 0 if (result.committed and not result.cancelled) else 1


def _handle_maintenance(args: argparse.Namespace) -> int:
    """Phase 5 融合收束：维护中心 CLI（health / jobs / reviews / source-event）。"""
    from src.core.container import create_container

    container = create_container()
    svc = container.wiki_maintenance_service
    sub = getattr(args, "maint_command", None) or "health"

    if sub == "health":
        snap = svc.health_snapshot()
        print(f"[OK] mode={snap.get('knowledge_mode')} automation={snap.get('automation_level')}")
        print(f"  servable_claims={snap.get('servable_claims')} stale_evidence={snap.get('stale_evidence')}")
        print(f"  open_reviews={snap.get('open_reviews')} failed_jobs={snap.get('failed_jobs')}")
        print(f"  claims={snap.get('claims')}")
        if snap.get("errors"):
            for e in snap["errors"]:
                print(f"  error: {e}")
        return 0

    if sub == "jobs":
        jobs = svc.list_jobs(status=getattr(args, "status", None), limit=getattr(args, "limit", 50))
        print(f"[OK] {len(jobs)} jobs")
        for j in jobs:
            print(f"  {j['job_id']} {j['job_type']} {j['risk_level']} {j['status']}")
        return 0

    if sub == "reviews":
        reviews = svc.list_reviews(
            status=getattr(args, "status", "open"),
            limit=getattr(args, "limit", 50),
        )
        print(f"[OK] {len(reviews)} reviews")
        for r in reviews:
            print(f"  {r['review_id']} {r['review_type']} {r['risk_level']} {r['status']} claim={r.get('claim_id')}")
        return 0

    if sub == "source-event":
        result = svc.handle_source_event(
            args.knowledge_id,
            args.event_type,
            source_path=getattr(args, "source_path", "") or "",
            human_confirmed=bool(getattr(args, "confirm", False)),
        )
        print(f"[OK] source-event handled: {result.get('job', {}).get('job_id')} "
              f"decision={result.get('decision', {}).get('decision')}")
        if result.get("review"):
            print(f"  review={result['review'].get('review_id')}")
        return 0 if result.get("ok", True) else 1

    if sub == "resolve":
        result = svc.resolve_review(
            args.review_id,
            args.action,
            operator=getattr(args, "operator", "cli"),
            note=getattr(args, "note", "") or "",
            human_confirmed=bool(getattr(args, "confirm", False)),
        )
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            return 1
        print(f"[OK] review {args.review_id} -> {result['review']['status']}")
        return 0

    if sub == "evaluate-r4":
        d = svc.evaluate_r4(args.job_type, human_confirmed=bool(getattr(args, "confirm", False)))
        print(f"[OK] decision={d.get('decision')} risk={d.get('risk_level')} reasons={d.get('reason_codes')}")
        return 0 if d.get("decision") != "block" or getattr(args, "confirm", False) else 2

    print(f"[ERROR] unknown maintenance command: {sub}", file=sys.stderr)
    return 1


def _handle_db(args: argparse.Namespace) -> int:
    """WP4: shinehe db status|backup|migrate|stamp|verify"""
    from src.storage.migration_cli import (
        MigrationWorkflowError,
        backup_database,
        db_status,
        migrate_database,
        stamp_database,
        verify_database,
    )
    from src.utils.config import Config

    Config.load(getattr(args, "config", None))
    db_path = Path(args.db) if getattr(args, "db", None) else Config.get_db_path()
    sub = getattr(args, "db_command", None)

    try:
        if sub == "status":
            info = db_status(db_path, config=Config)
            for k, v in info.items():
                print(f"{k}: {v}")
            return 0
        if sub == "backup":
            dest = backup_database(db_path)
            print(f"[OK] backup: {dest}")
            return 0
        if sub == "verify":
            result = verify_database(db_path)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("passed") else 2
        if sub == "stamp":
            result = stamp_database(
                db_path,
                from_version=args.from_version,
                force=bool(getattr(args, "force", False)),
            )
            print(f"[OK] stamped: {result}")
            return 0
        if sub == "migrate":
            result = migrate_database(db_path)
            print(f"[OK] migrate: {json.dumps(result, ensure_ascii=False, default=str)}")
            return 0
        print(f"[ERROR] unknown db command: {sub}", file=sys.stderr)
        return 1
    except MigrationWorkflowError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


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
        description=(
            "初始化 ShineHeKnowledge 项目，生成配置文件并可选配置 MCP 客户端。"
            " 默认 knowledge 模式为 verified（Raw + 已验证 Wiki 读）；"
            " authoring 开启完整 Wiki 维护；evidence-only 仅原始检索。"
        ),
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
        "--mode",
        default="verified",
        help=(
            "知识运行档位: verified（默认，只读已验证知识）| "
            "authoring（Wiki 维护）| evidence-only（仅原始检索）。"
            " 兼容旧值: wiki_first→authoring, legacy→evidence_only"
        ),
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
    doctor_parser.add_argument(
        "--explain-config", action="store_true",
        help="仅输出脱敏后的 raw/resolved 配置语义与迁移建议，不执行网络检查",
    )

    # --- config ---
    config_parser = subparsers.add_parser(
        "config", help="配置诊断与受控迁移",
    )
    config_sub = config_parser.add_subparsers(dest="config_command")
    config_migrate = config_sub.add_parser(
        "migrate-verified-hybrid",
        help="Verified Hybrid 配置迁移（默认 dry-run，apply 会备份并原子写入）",
    )
    config_migrate.add_argument("--config", default="config.yaml", help="目标配置文件")
    config_migrate.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    config_migrate.add_argument("--apply", action="store_true", help="备份后原子写入")
    config_migrate.add_argument("--rollback", default=None, help="从指定 .bak 文件恢复字节级原配置")
    config_migrate.add_argument(
        "--target-mode", default="keep", choices=["keep", "verified", "authoring", "evidence_only"],
        help="keep 仅规范旧别名；其余值明确切换运行档位",
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

    # --- wiki (嵌套子命令组) ---
    wiki_parser = subparsers.add_parser(
        "wiki", help="wiki-first 知识维护",
        description="wiki 编译/检索闭环:lint / save-answer / ingest-source / migrate-v2 / validate / claims。",
    )
    wiki_sub = wiki_parser.add_subparsers(dest="wiki_command", help="wiki 子命令")

    lint_p = wiki_sub.add_parser("lint", help="运行 wiki 健康检查")
    lint_p.add_argument(
        "--source", choices=["auto", "fs", "sqlite"], default="auto",
        help="lint 数据源:auto(按 mode) / fs(wiki/*.md) / sqlite(旧表)",
    )
    save_p = wiki_sub.add_parser("save-answer", help="保存问答为 wiki 综合页")
    save_p.add_argument("--question", required=True, help="问题")
    save_p.add_argument("--answer", required=True, help="回答")
    ingest_p = wiki_sub.add_parser("ingest-source", help="ingest 单源并触发 wiki 编译")
    ingest_p.add_argument("path", help="源文件路径")

    # Phase 6: migrate-v2 / validate / claims
    mv2 = wiki_sub.add_parser("migrate-v2", help="A/B 轨 → Canonical Store 迁移(Phase 6)")
    mv2.add_argument("--apply", action="store_true", help="执行迁移(默认 dry-run)")
    mv2.add_argument("--rollback", metavar="TIMESTAMP", help="从 backups/wiki-v2-<ts> 回滚")

    val_p = wiki_sub.add_parser("validate", help="校验 claim provenance / 目录 invariant")
    val_p.add_argument("--strict", action="store_true", help="有 error 时非零退出")

    svm_p = wiki_sub.add_parser(
        "serving-validation-migration",
        help="预览缺少 Serving Validation 证明的 Active Claim（只读）",
    )
    svm_p.add_argument("--dry-run", action="store_true", help="只读预览（默认）")
    svm_p.add_argument("--apply", action="store_true", help="Phase 8 前不可用")

    claims_p = wiki_sub.add_parser("claims", help="查看/反馈 claim")
    claims_sub = claims_p.add_subparsers(dest="claims_command")
    claims_list = claims_sub.add_parser("list", help="列出 claims")
    claims_list.add_argument("--status", default=None, help="按状态过滤")
    claims_show = claims_sub.add_parser("show", help="显示 claim 详情")
    claims_show.add_argument("claim_id", help="claim id")
    claims_rev = claims_sub.add_parser("review", help="对 claim 施加反馈")
    claims_rev.add_argument("claim_id", help="claim id")
    claims_rev.add_argument(
        "--action", required=True,
        choices=["confirm", "reject", "correct", "needs_review"],
        help="反馈动作",
    )
    claims_rev.add_argument("--correction", default=None, help="correct 时的修正文案")
    claims_rev.add_argument("--operator", default="cli", help="操作者")
    claims_rev.add_argument("--note", default="", help="备注")

    # --- migrate ---
    migrate_parser = subparsers.add_parser(
        "migrate", help="迁移 legacy 项目到 wiki-first",
        description="扫描 data/ 知识,导出源到 raw/,触发 wiki 重编译。默认 dry-run。",
    )
    migrate_parser.add_argument("--apply", action="store_true", help="执行迁移(默认仅计划)")
    migrate_parser.add_argument("--no-backup", action="store_true", help="apply 时跳过 data/ 备份")

    # --- rebuild (Phase 5) ---
    rebuild_parser = subparsers.add_parser(
        "rebuild", help="来源失效重建(Phase 5)",
        description="对指定 knowledge_id 触发依赖失效重建:标记 stale evidence、迁移 claim/page、刷新 projection。",
    )
    rebuild_parser.add_argument("--knowledge-id", required=True, help="目标 knowledge_id")
    rebuild_parser.add_argument(
        "--event", choices=["update", "delete"], default="update", help="事件类型(默认 update)",
    )
    rebuild_parser.add_argument("--dry-run", action="store_true", help="仅规划,不写")

    # --- maintenance (融合收束 Phase 5) ---
    maint_parser = subparsers.add_parser(
        "maintenance", help="Wiki 维护中心",
        description="健康快照、来源事件、维护任务、审阅队列与 R4 策略预检。",
    )
    maint_sub = maint_parser.add_subparsers(dest="maint_command", help="maintenance 子命令")
    maint_sub.add_parser("health", help="健康快照")
    jobs_p = maint_sub.add_parser("jobs", help="列出维护任务")
    jobs_p.add_argument("--status", default=None)
    jobs_p.add_argument("--limit", type=int, default=50)
    rev_p = maint_sub.add_parser("reviews", help="列出审阅项")
    rev_p.add_argument("--status", default="open")
    rev_p.add_argument("--limit", type=int, default=50)
    se_p = maint_sub.add_parser("source-event", help="处理来源变更事件")
    se_p.add_argument("--knowledge-id", required=True)
    se_p.add_argument("--event-type", default="updated", choices=["created", "updated", "deleted"])
    se_p.add_argument("--source-path", default="")
    se_p.add_argument("--confirm", action="store_true", help="人工确认（R4）")
    res_p = maint_sub.add_parser("resolve", help="审阅决议")
    res_p.add_argument("review_id")
    res_p.add_argument(
        "--action", required=True,
        choices=["confirm", "reject", "correct", "needs_review", "defer"],
    )
    res_p.add_argument("--operator", default="cli")
    res_p.add_argument("--note", default="")
    res_p.add_argument("--confirm", action="store_true")
    r4_p = maint_sub.add_parser("evaluate-r4", help="R4 策略预检")

    # --- db (WP4 legacy migration) ---
    db_parser = subparsers.add_parser(
        "db",
        help="数据库迁移治理（status/backup/migrate/stamp/verify）",
    )
    db_parser.add_argument(
        "--db",
        default=None,
        help="SQLite 路径（默认 Config storage 路径；禁止隐式改用户库请显式传入）",
    )
    db_parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径",
    )
    db_sub = db_parser.add_subparsers(dest="db_command", help="db 子命令")
    db_sub.add_parser("status", help="迁移状态与推荐操作")
    db_sub.add_parser("backup", help="SQLite Backup API 备份")
    db_sub.add_parser("verify", help="integrity / 行数 / head 校验")
    db_sub.add_parser("migrate", help="备份→识别→stamp→upgrade head→校验")
    stamp_p = db_sub.add_parser(
        "stamp",
        help="仅 stamp（需 --from-version 且 detector 高置信匹配；禁止 stamp head）",
    )
    stamp_p.add_argument(
        "--from-version",
        required=True,
        help="历史版本 id，如 v1.9.x / v1.9.x+maintenance（禁止 head）",
    )
    stamp_p.add_argument(
        "--force",
        action="store_true",
        help="允许 stamp 目标与 detector 不完全一致（危险）",
    )
    r4_p.add_argument("--job-type", default="publish")
    r4_p.add_argument("--confirm", action="store_true")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "init": _handle_init,
        "index": _handle_index,
        "watch": _handle_watch,
        "doctor": _handle_doctor,
        "config": _handle_config,
        "mcp": _handle_mcp,
        "wiki": _handle_wiki,
        "migrate": _handle_migrate,
        "rebuild": _handle_rebuild,
        "maintenance": _handle_maintenance,
        "db": _handle_db,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    exit_code = handler(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
