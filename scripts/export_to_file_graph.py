"""Export existing DB knowledge items to the file-first Markdown graph.

Usage:
    python scripts/export_to_file_graph.py              # dry-run
    python scripts/export_to_file_graph.py --apply      # write files
    python scripts/export_to_file_graph.py --apply --no-backup
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.container import create_container, shutdown_container  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Export DB items to local Markdown graph")
    parser.add_argument("--apply", action="store_true", help="Write graph files")
    parser.add_argument("--no-backup", action="store_true", help="Do not back up existing graph")
    args = parser.parse_args()

    container = create_container()
    try:
        service = container.file_graph_service
        graph_dir = service.ensure_graph()
        if args.apply and not args.no_backup and any((graph_dir / "pages").glob("*.md")):
            backup_dir = graph_dir / ".kb" / "backups" / datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir.mkdir(parents=True, exist_ok=True)
            for page in (graph_dir / "pages").glob("*.md"):
                shutil.copy2(page, backup_dir / page.name)
        result = service.export_db_to_graph(dry_run=not args.apply, backup=not args.no_backup)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        shutdown_container(container)


if __name__ == "__main__":
    main()
