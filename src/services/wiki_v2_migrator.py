"""WikiV2Migrator — A/B 轨 → Canonical Store 迁移(Phase 6)。

顺序(纠偏方案 §6 / Spec §11):
  dry-run → backup → isolated canonical generation → claim review report
  → validation → projection rebuild → parity → primary *suggestion*

铁律:
- dry-run 零写入
- apply 必须 lock + 备份;失败不半写
- 不自动强制 canonical_v2.mode=primary
- 无来源 facts 不得自动 active
- 构造函数 DI,禁全局 Config/Database/container
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceStance,
    PageStatus,
    PageType,
    WikiPage,
    normalize_statement,
)
from src.services.wiki_slug import read_frontmatter
from src.services.wiki_source_ids import _parse_json_list, resolve_source_ids

logger = logging.getLogger(__name__)

_PAGE_TYPE_DIRS = ("sources", "entities", "concepts", "comparisons", "syntheses")
_FACTS_HEADING_RE = re.compile(r"^##\s+Facts\s*$", re.IGNORECASE | re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+)$")
_SIMILARITY_MATCH = 0.85
_LOCK_NAME = ".wiki_v2_migration.lock"


@dataclass
class MigrationPagePlan:
    track: str  # a | b | matched | conflict
    source_ref: str
    title: str
    page_type: str
    source_ids: list[str]
    match_page_id: str | None
    action: str  # create | skip_already_canonical | conflict | match_a_b
    body: str = ""
    reasons: list[str] = field(default_factory=list)


@dataclass
class MigrationClaimPlan:
    statement: str
    source_ids: list[str]
    page_title: str
    status: str  # draft | unsupported
    location_quality: str  # page_only | missing
    action: str = "create"
    page_type: str = "entities"
    page_id: str | None = None


@dataclass
class MigrationReport:
    mode: str  # dry_run | apply | rollback
    a_page_count: int = 0
    b_page_count: int = 0
    already_canonical: int = 0
    matched: int = 0
    conflicts: int = 0
    pages_to_create: int = 0
    claims_to_create: int = 0
    untraceable_facts: int = 0
    backup_path: str = ""
    lock_held: bool = False
    cutover_ready: bool = False
    suggestion: str = ""
    page_plans: list[MigrationPagePlan] = field(default_factory=list)
    claim_plans: list[MigrationClaimPlan] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    writes: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def content_similarity(a: str, b: str) -> float:
    """字符 bigram Jaccard 相似度。"""
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba and not bb:
        return 1.0
    if not ba or not bb:
        return 0.0
    inter = len(ba & bb)
    union = len(ba | bb)
    return inter / union if union else 0.0


def parse_facts(body: str) -> list[str]:
    """从 body 解析 ## Facts 下的 bullet 列表。无 heading 时返回 []。"""
    if not body:
        return []
    m = _FACTS_HEADING_RE.search(body)
    if not m:
        return []
    rest = body[m.end() :]
    # 截到下一个 ## heading
    next_h = re.search(r"^##\s+", rest, re.MULTILINE)
    section = rest[: next_h.start()] if next_h else rest
    facts: list[str] = []
    for line in section.splitlines():
        bm = _BULLET_RE.match(line)
        if bm:
            fact = bm.group(1).strip()
            if fact:
                facts.append(fact)
    return facts


def _read_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    return parts[2].lstrip("\n")


def _content_hash(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _default_clock() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _default_id() -> str:
    return str(uuid.uuid4())


class WikiV2Migrator:
    def __init__(
        self,
        wiki_dir: Path | str,
        repository,
        *,
        database=None,
        projection=None,
        backups_dir: Path | str | None = None,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[], str] | None = None,
        config: dict | None = None,
    ):
        self._wiki_dir = Path(wiki_dir)
        self._repo = repository
        self._database = database
        self._projection = projection
        self._backups_dir = Path(backups_dir) if backups_dir else self._wiki_dir.parent / "backups"
        self._clock = clock or _default_clock
        self._id_factory = id_factory or _default_id
        self._config = config

    # ------------------------------------------------------------------ plan
    def dry_run(self) -> MigrationReport:
        report = MigrationReport(mode="dry_run", writes=0)
        a_pages = self._scan_a_pages()
        b_pages = self._scan_b_pages()
        report.a_page_count = len(a_pages)
        report.b_page_count = len(b_pages)

        registry = self._repo.get_registry() if self._repo else {}
        existing_canonical_ids = set(registry.keys())
        # 也把 repo.list_pages 的 id 并入(registry 空但文件已有的情况)
        try:
            for p in self._repo.list_pages():
                existing_canonical_ids.add(p.page_id)
        except Exception:
            logger.debug("list_pages during dry_run failed", exc_info=True)

        used_a: set[str] = set()
        page_plans: list[MigrationPagePlan] = []
        claim_plans: list[MigrationClaimPlan] = []

        # B 轨优先
        for bp in b_pages:
            pid = bp.get("page_id") or ""
            title = bp["title"]
            body = bp["body"]
            source_ids = list(bp["source_ids"])
            page_type = bp["page_type"]
            rel = bp["source_ref"]

            if pid and pid in existing_canonical_ids:
                page_plans.append(MigrationPagePlan(
                    track="b", source_ref=rel, title=title, page_type=page_type,
                    source_ids=source_ids, match_page_id=pid,
                    action="skip_already_canonical", body=body,
                    reasons=["explicit page_id already in canonical registry"],
                ))
                report.already_canonical += 1
                continue

            if pid and not existing_canonical_ids:
                # 有 page_id 但 registry 空:可能是半 canonical 文件,仍跳过 create
                # 若 repo 尚未登记,apply 时会 stage 一次以注册
                page_plans.append(MigrationPagePlan(
                    track="b", source_ref=rel, title=title, page_type=page_type,
                    source_ids=source_ids, match_page_id=pid,
                    action="skip_already_canonical", body=body,
                    reasons=["explicit page_id present on filesystem page"],
                ))
                report.already_canonical += 1
                continue

            # 尝试与 A 轨匹配
            match_a, action, reasons = self._match_a(bp, a_pages, used_a)
            if action == "conflict":
                if match_a:
                    used_a.add(match_a["id"])
                page_plans.append(MigrationPagePlan(
                    track="conflict", source_ref=rel, title=title, page_type=page_type,
                    source_ids=source_ids, match_page_id=match_a["id"] if match_a else None,
                    action="conflict", body=body, reasons=reasons,
                ))
                # 冲突双方均保留为独立待审项,不自动 create 合并
                page_plans.append(MigrationPagePlan(
                    track="a", source_ref=f"a:{match_a['id']}" if match_a else "a:?",
                    title=match_a["title"] if match_a else title,
                    page_type=match_a.get("page_type", "concepts") if match_a else "concepts",
                    source_ids=list(match_a.get("source_ids") or []) if match_a else [],
                    match_page_id=match_a["id"] if match_a else None,
                    action="conflict", body=match_a.get("content", "") if match_a else "",
                    reasons=reasons,
                ))
                report.conflicts += 1
                continue

            if action == "match_a_b" and match_a:
                used_a.add(match_a["id"])
                new_id = match_a["id"]
                # 合并 source_ids
                merged_sids = sorted(set(source_ids) | set(match_a.get("source_ids") or []))
                page_plans.append(MigrationPagePlan(
                    track="matched", source_ref=rel, title=title, page_type=page_type,
                    source_ids=merged_sids, match_page_id=new_id,
                    action="match_a_b", body=body or match_a.get("content", ""),
                    reasons=reasons,
                ))
                report.matched += 1
                source_ids = merged_sids
            else:
                new_id = self._id_factory()
                page_plans.append(MigrationPagePlan(
                    track="b", source_ref=rel, title=title, page_type=page_type,
                    source_ids=source_ids, match_page_id=new_id,
                    action="create", body=body, reasons=["b-track only"],
                ))
                report.pages_to_create += 1

            # Claims from facts
            facts = parse_facts(body)
            for fact in facts:
                if source_ids:
                    claim_plans.append(MigrationClaimPlan(
                        statement=fact, source_ids=source_ids, page_title=title,
                        status="draft", location_quality="page_only", action="create",
                        page_type=page_type, page_id=new_id if action != "conflict" else None,
                    ))
                else:
                    claim_plans.append(MigrationClaimPlan(
                        statement=fact, source_ids=[], page_title=title,
                        status="unsupported", location_quality="missing", action="create",
                        page_type=page_type, page_id=new_id if action != "conflict" else None,
                    ))
                    report.untraceable_facts += 1

        # 未匹配的 A 轨 → create
        for ap in a_pages:
            if ap["id"] in used_a:
                continue
            if ap["id"] in existing_canonical_ids:
                report.already_canonical += 1
                page_plans.append(MigrationPagePlan(
                    track="a", source_ref=f"a:{ap['id']}", title=ap["title"],
                    page_type=ap.get("page_type", "concepts"),
                    source_ids=list(ap.get("source_ids") or []),
                    match_page_id=ap["id"], action="skip_already_canonical",
                    body=ap.get("content", ""),
                    reasons=["a-track id already canonical"],
                ))
                continue
            page_plans.append(MigrationPagePlan(
                track="a", source_ref=f"a:{ap['id']}", title=ap["title"],
                page_type=ap.get("page_type", "concepts"),
                source_ids=list(ap.get("source_ids") or []),
                match_page_id=ap["id"], action="create",
                body=ap.get("content", ""),
                reasons=["a-track only"],
            ))
            report.pages_to_create += 1
            facts = parse_facts(ap.get("content", "") or "")
            sids = list(ap.get("source_ids") or [])
            for fact in facts:
                if sids:
                    claim_plans.append(MigrationClaimPlan(
                        statement=fact, source_ids=sids, page_title=ap["title"],
                        status="draft", location_quality="page_only",
                        page_type=ap.get("page_type", "concepts"), page_id=ap["id"],
                    ))
                else:
                    claim_plans.append(MigrationClaimPlan(
                        statement=fact, source_ids=[], page_title=ap["title"],
                        status="unsupported", location_quality="missing",
                        page_type=ap.get("page_type", "concepts"), page_id=ap["id"],
                    ))
                    report.untraceable_facts += 1

        report.page_plans = page_plans
        report.claim_plans = claim_plans
        report.claims_to_create = sum(1 for c in claim_plans if c.action == "create")
        report.cutover_ready = (
            report.conflicts == 0
            and not report.errors
            and (report.pages_to_create + report.matched + report.already_canonical) > 0
        )
        if report.cutover_ready:
            report.suggestion = (
                "Migration plan looks healthy; after apply + human review, "
                "consider setting wiki.canonical_v2.mode=primary (not auto-enabled)."
            )
        elif report.conflicts:
            report.suggestion = "Resolve conflicts before apply; do not force-merge same-title pages."
        else:
            report.suggestion = "Review dry-run report before apply."
        return report

    def _match_a(
        self,
        bp: dict,
        a_pages: list[dict],
        used_a: set[str],
    ) -> tuple[dict | None, str, list[str]]:
        """返回 (matched_a_page | None, action, reasons)。"""
        b_title_n = _normalize_title(bp["title"])
        b_sids = set(bp.get("source_ids") or [])
        b_body = bp.get("body") or ""
        b_aliases = {_normalize_title(a) for a in (bp.get("aliases") or [])}

        # 1) source_ids + title
        for ap in a_pages:
            if ap["id"] in used_a:
                continue
            a_sids = set(ap.get("source_ids") or [])
            if b_sids and a_sids and b_sids == a_sids and _normalize_title(ap["title"]) == b_title_n:
                return ap, "match_a_b", ["same source_ids + normalized title"]

        # 2) title + high content similarity
        title_candidates = [
            ap for ap in a_pages
            if ap["id"] not in used_a and _normalize_title(ap["title"]) == b_title_n
        ]
        for ap in title_candidates:
            sim = content_similarity(b_body, ap.get("content") or "")
            if sim >= _SIMILARITY_MATCH:
                return ap, "match_a_b", [f"same title + content_similarity={sim:.2f}"]
            # 同名但内容差异大 → conflict
            if sim < _SIMILARITY_MATCH:
                return ap, "conflict", [
                    f"same title but content_similarity={sim:.2f} < {_SIMILARITY_MATCH}",
                ]

        # 3) aliases
        for ap in a_pages:
            if ap["id"] in used_a:
                continue
            a_title_n = _normalize_title(ap["title"])
            if a_title_n in b_aliases or b_title_n in {
                _normalize_title(x) for x in (ap.get("aliases") or [])
            }:
                return ap, "match_a_b", ["aliases hit"]

        return None, "create", ["no a-track match"]

    # ----------------------------------------------------------------- apply
    def apply(self) -> MigrationReport:
        lock_path = self._backups_dir / _LOCK_NAME
        self._backups_dir.mkdir(parents=True, exist_ok=True)

        if lock_path.exists():
            report = MigrationReport(mode="apply", writes=0)
            report.errors.append(f"migration lock held: {lock_path}")
            return report

        try:
            lock_path.write_text(self._clock(), encoding="utf-8")
        except OSError as e:
            report = MigrationReport(mode="apply", writes=0)
            report.errors.append(f"cannot acquire lock: {e}")
            return report

        report = MigrationReport(mode="apply", lock_held=True)
        try:
            plan = self.dry_run()
            report.a_page_count = plan.a_page_count
            report.b_page_count = plan.b_page_count
            report.already_canonical = plan.already_canonical
            report.matched = plan.matched
            report.conflicts = plan.conflicts
            report.page_plans = plan.page_plans
            report.claim_plans = plan.claim_plans
            report.untraceable_facts = plan.untraceable_facts
            report.pages_to_create = plan.pages_to_create
            report.claims_to_create = plan.claims_to_create

            if plan.conflicts:
                report.warnings.append(
                    f"{plan.conflicts} conflict(s) will not be auto-merged; "
                    "conflict pages skipped"
                )

            ts = self._clock()
            backup_root = self._backups_dir / f"wiki-v2-{ts}"
            backup_root.mkdir(parents=True, exist_ok=True)
            wiki_backup = backup_root / "wiki"
            if self._wiki_dir.exists():
                shutil.copytree(self._wiki_dir, wiki_backup, dirs_exist_ok=True)
            report.backup_path = str(backup_root)

            # 可选 config 快照(不修改 config)
            if self._config is not None:
                (backup_root / "config_snapshot.json").write_text(
                    json.dumps(self._config, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )

            now = datetime.now(timezone.utc).isoformat()
            writes = 0
            # page_title → (page_id, claim_ids)
            page_claim_map: dict[str, list[str]] = {}
            page_id_by_title: dict[str, str] = {}

            # 先创建 claims,再写 pages(带 claim_ids)
            staged_claims: list[Claim] = []
            for cp in plan.claim_plans:
                if cp.action != "create":
                    continue
                cid = self._id_factory()
                evidence: list[Evidence] = []
                status = ClaimStatus.DRAFT if cp.status == "draft" else ClaimStatus.UNSUPPORTED
                for sid in cp.source_ids:
                    evidence.append(Evidence(
                        evidence_id=self._id_factory(),
                        stance=EvidenceStance.SUPPORTS,
                        knowledge_id=sid,
                        block_id=None,
                        location={"quality": cp.location_quality},
                        source_revision="migration",
                        excerpt_hash=None,
                        observed_at=now,
                    ))
                # unsupported 且无 evidence 时不要标 active(已是 unsupported)
                claim = Claim(
                    schema_version=1,
                    claim_id=cid,
                    statement=cp.statement,
                    normalized_statement=normalize_statement(cp.statement),
                    claim_type="fact",
                    status=status,
                    confidence=0.5,
                    valid_from=None,
                    valid_to=None,
                    subject_refs=[],
                    predicate="migrated_fact",
                    object_refs=[],
                    evidence=evidence,
                    relations=[],
                    created_at=now,
                    updated_at=now,
                    revision=1,
                )
                staged_claims.append(claim)
                page_claim_map.setdefault(cp.page_title, []).append(cid)
                if cp.page_id:
                    page_id_by_title[cp.page_title] = cp.page_id

            staged_pages: list[WikiPage] = []
            for pp in plan.page_plans:
                if pp.action in ("skip_already_canonical", "conflict"):
                    continue
                if pp.action not in ("create", "match_a_b"):
                    continue
                pid = pp.match_page_id or self._id_factory()
                page_id_by_title[pp.title] = pid
                try:
                    pt = PageType(pp.page_type) if pp.page_type in PageType._value2member_map_ else PageType.CONCEPTS
                except (ValueError, KeyError):
                    pt = PageType.CONCEPTS
                    report.warnings.append(f"unknown page_type {pp.page_type!r} for {pp.title}; using concepts")
                claim_ids = page_claim_map.get(pp.title, [])
                body = pp.body or ""
                page = WikiPage(
                    schema_version=1,
                    page_id=pid,
                    title=pp.title,
                    page_type=pt,
                    status=PageStatus.DRAFT,  # 迁移页默认 draft,人工 review
                    revision=1,
                    aliases=[],
                    tags=[],
                    source_ids=list(pp.source_ids),
                    claim_ids=claim_ids,
                    created_at=now,
                    updated_at=now,
                    content_hash=_content_hash(body),
                    body=body,
                )
                staged_pages.append(page)

            if staged_pages or staged_claims:
                with self._repo.transaction() as tx:
                    for c in staged_claims:
                        tx.stage_claim(c)
                    for p in staged_pages:
                        tx.stage_page(p)
                writes = len(staged_pages) + len(staged_claims)

            report.writes = writes
            report.pages_to_create = len(staged_pages)
            report.claims_to_create = len(staged_claims)

            # projection 可选
            if self._projection is not None:
                try:
                    if hasattr(self._projection, "process_outbox"):
                        self._projection.process_outbox(force=True)
                    if hasattr(self._projection, "verify_parity"):
                        findings = self._projection.verify_parity()
                        if findings:
                            report.warnings.append(f"projection parity findings: {len(findings)}")
                except Exception as e:
                    report.warnings.append(f"projection refresh failed: {e}")
                    logger.warning("projection during migrate apply failed", exc_info=True)

            report.cutover_ready = report.conflicts == 0 and not report.errors and writes >= 0
            report.suggestion = (
                "Apply completed; review draft claims/pages. "
                "Do NOT auto-enable primary — set wiki.canonical_v2.mode=primary only after validation."
            )
            # 明确不修改 config
            if self._config is not None:
                mode = (
                    self._config.get("wiki", {})
                    .get("canonical_v2", {})
                    .get("mode", "off")
                )
                if mode != "off":
                    report.warnings.append(
                        f"config mode is {mode!r}; migrator did not change it"
                    )

            # migration-report.json
            (backup_root / "migration-report.json").write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return report
        except Exception as e:
            report.errors.append(str(e))
            logger.exception("migration apply failed")
            return report
        finally:
            try:
                if lock_path.exists():
                    lock_path.unlink()
            except OSError:
                logger.warning("failed to release migration lock %s", lock_path, exc_info=True)

    # -------------------------------------------------------------- rollback
    def rollback(self, timestamp: str) -> MigrationReport:
        report = MigrationReport(mode="rollback", writes=0)
        backup_root = self._backups_dir / f"wiki-v2-{timestamp}"
        wiki_backup = backup_root / "wiki"
        if not wiki_backup.exists():
            report.errors.append(f"backup not found: {wiki_backup}")
            return report

        # 当前 wiki 先挪走,再恢复备份
        if self._wiki_dir.exists():
            pre = backup_root / "pre-rollback-wiki"
            if pre.exists():
                shutil.rmtree(pre, ignore_errors=True)
            shutil.move(str(self._wiki_dir), str(pre))
        shutil.copytree(wiki_backup, self._wiki_dir)
        report.backup_path = str(backup_root)
        report.writes = 1  # restore counts as a write operation for audit
        report.suggestion = (
            f"Rolled back wiki from {backup_root}. Raw sources were not modified."
        )
        # 恢复后 repository 的 registry 以文件为准;调用方应重建 projection
        if self._projection is not None and hasattr(self._projection, "rebuild"):
            try:
                self._projection.rebuild()
            except Exception as e:
                report.warnings.append(f"projection rebuild after rollback failed: {e}")
        return report

    # ---------------------------------------------------------------- scans
    def _scan_b_pages(self) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        if not self._wiki_dir.exists():
            return pages
        for pt in _PAGE_TYPE_DIRS:
            d = self._wiki_dir / pt
            if not d.exists():
                continue
            for md in sorted(d.glob("*.md")):
                if md.name.startswith("."):
                    continue
                fm = read_frontmatter(md)
                if not fm and not md.read_text(encoding="utf-8").strip():
                    continue
                title = str(fm.get("title") or md.stem)
                page_type = str(fm.get("page_type") or pt)
                body = _read_body(md)
                sids = resolve_source_ids(fm)
                pages.append({
                    "source_ref": f"b:{pt}/{md.name}",
                    "path": md,
                    "title": title,
                    "page_type": page_type,
                    "page_id": fm.get("page_id") or "",
                    "source_ids": sids,
                    "aliases": list(fm.get("aliases") or []),
                    "body": body,
                    "claim_ids": list(fm.get("claim_ids") or []),
                })
        return pages

    def _scan_a_pages(self) -> list[dict[str, Any]]:
        if self._database is None:
            return []
        try:
            rows = self._database.list_wiki_pages(limit=10000)  # type: ignore[call-arg]
        except TypeError:
            rows = self._database.list_wiki_pages()
        except Exception:
            logger.debug("list_wiki_pages failed", exc_info=True)
            return []
        out: list[dict[str, Any]] = []
        for row in rows or []:
            if row.get("status") == "deleted":
                continue
            sids = _parse_json_list(row.get("source_ids"))
            out.append({
                "id": row["id"],
                "title": row.get("title") or row["id"],
                "content": row.get("content") or "",
                "source_ids": sids,
                "tags": _parse_json_list(row.get("tags")),
                "aliases": [],
                "page_type": "concepts",  # A 轨无 page_type,默认 concepts
                "status": row.get("status") or "active",
            })
        return out
