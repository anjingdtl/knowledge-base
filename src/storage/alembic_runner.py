"""Explicit Alembic upgrade service (WP3).

Always targets an explicit DB path/URL — never falls back to the user default
database from Config.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from src.storage.migration_status import get_alembic_head, get_current_revision

logger = logging.getLogger(__name__)


class AlembicUpgradeError(RuntimeError):
    """Alembic upgrade failed for the requested database path."""


@dataclass(frozen=True)
class AlembicUpgradeResult:
    db_path: str
    before_revision: str | None
    after_revision: str
    upgraded: bool


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sqlite_url_for_path(db_path: str | Path) -> str:
    path = Path(db_path).resolve()
    return f"sqlite:///{path.as_posix()}"


def _run_alembic_upgrade(
    db_path: Path,
    *,
    project_root: Path,
) -> subprocess.CompletedProcess[str]:
    """Invoke ``alembic upgrade head`` with an explicit SHINEHE_TEST_ALEMBIC_URL."""
    url = sqlite_url_for_path(db_path)
    env = os.environ.copy()
    # Force env.py to use this URL; never Config.get_db_path() fallback.
    env["SHINEHE_TEST_ALEMBIC_URL"] = url
    # Avoid nested gate interference
    env.pop("SHINEHE_ENFORCE_MIGRATION_GATE", None)

    cmd = [sys.executable, "-m", "alembic", "upgrade", "head"]
    logger.info("Running alembic upgrade head for %s", db_path)
    result = subprocess.run(
        cmd,
        cwd=str(project_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        raise AlembicUpgradeError(
            f"alembic upgrade head failed for {db_path} "
            f"(exit={result.returncode}):\n{combined[-4000:]}"
        )
    if result.stdout:
        logger.debug("alembic stdout: %s", result.stdout[-2000:])
    if result.stderr:
        logger.debug("alembic stderr: %s", result.stderr[-2000:])
    return result


def upgrade_to_head(
    db_path: str | Path,
    *,
    project_root: Path | None = None,
) -> AlembicUpgradeResult:
    """Upgrade the given SQLite database to Alembic head.

    Parameters
    ----------
    db_path:
        Explicit path to the target database. Required — no default user DB.
    project_root:
        Project root containing alembic.ini (defaults to repository root).
    """
    if db_path is None:
        raise ValueError("db_path is required; refuse to use default user database")

    path = Path(db_path)
    root = project_root or _default_project_root()
    if not (root / "alembic.ini").is_file():
        raise AlembicUpgradeError(f"alembic.ini not found under project_root={root}")

    # Ensure parent directory exists so SQLite can create the file
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    before = get_current_revision(path) if path.is_file() else None
    head = get_alembic_head(root)

    if before == head:
        return AlembicUpgradeResult(
            db_path=str(path.resolve()) if path.exists() else str(path),
            before_revision=before,
            after_revision=head,
            upgraded=False,
        )

    try:
        _run_alembic_upgrade(path, project_root=root)
    except AlembicUpgradeError:
        raise
    except Exception as exc:
        raise AlembicUpgradeError(
            f"alembic upgrade head failed for {path}: {exc}"
        ) from exc

    after = get_current_revision(path)
    if after != head:
        raise AlembicUpgradeError(
            f"after upgrade expected head={head}, got revision={after} for {path}"
        )

    return AlembicUpgradeResult(
        db_path=str(path.resolve()),
        before_revision=before,
        after_revision=after,
        upgraded=True,
    )


def stamp_to_revision(
    db_path: str | Path,
    revision: str,
    *,
    project_root: Path | None = None,
) -> str:
    """Stamp alembic_version without running migrations (explicit revision only).

    Never accepts bare ``head`` as a free-form stamp of unknown schema; caller
    must resolve a concrete revision id first.
    """
    if not revision or revision.strip().lower() == "head":
        raise AlembicUpgradeError(
            "refusing to stamp free-form 'head' onto unknown schema; "
            "pass an explicit revision id from a high-confidence detector match"
        )
    path = Path(db_path)
    if not path.is_file():
        raise AlembicUpgradeError(f"cannot stamp missing database: {path}")
    root = project_root or _default_project_root()
    url = sqlite_url_for_path(path)
    env = os.environ.copy()
    env["SHINEHE_TEST_ALEMBIC_URL"] = url
    env.pop("SHINEHE_ENFORCE_MIGRATION_GATE", None)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "stamp", revision],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        raise AlembicUpgradeError(
            f"alembic stamp {revision} failed for {path}:\n{combined[-3000:]}"
        )
    current = get_current_revision(path)
    if current != revision:
        raise AlembicUpgradeError(
            f"after stamp expected {revision}, got {current}"
        )
    return str(current)
