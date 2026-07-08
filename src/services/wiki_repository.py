"""Canonical Wiki filesystem Repository(spec §4.2 ADR-002)。

唯一 canonical 写入口:页面 Markdown + Claim YAML + registry + redirects + outbox。
- 原子写:复用 wiki_slug.write_markdown(tempfile + os.replace);Claim YAML 同模式
- revision 乐观锁:save 时比对 expected_revision,失配抛 StaleRevisionError
- transaction:轻量实现 — stage 仅记对象,commit 才落盘,中途失败天然无残留
  (spec §14.1 的严格 _staging/<tx_id> 落盘方案在 Phase 2/4 增强)
- 路径安全:所有 canonical 路径必须在 wiki_dir 内,禁 ..
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Iterator, cast

import yaml

from src.models.wiki_v2 import (
    Claim,
    ClaimStatus,
    PageRegistryEntry,
    PageType,
    SaveResult,
    WikiPage,
)
from src.services.wiki_slug import read_frontmatter, slugify, write_markdown


class StaleRevisionError(RuntimeError):
    """expected_revision 与磁盘当前 revision 不符(lost update 防护)。"""


class WikiTransaction:
    """staging 事务:stage 暂存,commit 原子发布,异常自动丢弃 staging。"""

    def __init__(self, repo: "WikiRepository", tx_id: str):
        self._repo = repo
        self._tx_id = tx_id
        self._staged_pages: list[tuple[WikiPage, int | None]] = []
        self._staged_claims: list[tuple[Claim, int | None]] = []
        self._committed = False

    def stage_page(self, page: WikiPage, expected_revision: int | None = None) -> None:
        self._staged_pages.append((page, expected_revision))

    def stage_claim(self, claim: Claim, expected_revision: int | None = None) -> None:
        self._staged_claims.append((claim, expected_revision))

    def commit(self) -> list[SaveResult]:
        results: list[SaveResult] = []
        for page, exp in self._staged_pages:
            results.append(self._repo.save_page(page, expected_revision=exp))
        for claim, exp in self._staged_claims:
            results.append(self._repo.save_claim(claim, expected_revision=exp))
        self._committed = True
        return results


class WikiRepository:
    def __init__(
        self,
        wiki_dir: Path | str,
        registry_path: Path | str,
        redirects_path: Path | str,
        outbox_path: Path | str,
        validator=None,
    ):
        self._wiki_dir = Path(wiki_dir)
        self._registry_path = Path(registry_path)
        self._redirects_path = Path(redirects_path)
        self._outbox_path = Path(outbox_path)
        self._validator = validator
        self._lock = threading.RLock()
        self._wiki_dir.mkdir(parents=True, exist_ok=True)
        (self._wiki_dir / "claims").mkdir(exist_ok=True)
        (self._wiki_dir / "_meta").mkdir(exist_ok=True)
        (self._wiki_dir / "_staging").mkdir(exist_ok=True)

    # ---- 路径解析 ----
    def _page_path(self, page_type: PageType, title: str) -> Path:
        return self._wiki_dir / page_type.value / f"{slugify(title)}.md"

    def _claim_path(self, claim_id: str) -> Path:
        return self._wiki_dir / "claims" / f"{claim_id}.yaml"

    def _rel(self, abs_path: Path) -> str:
        return str(abs_path.relative_to(self._wiki_dir)).replace("\\", "/")

    def _assert_inside_wiki(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self._wiki_dir.resolve())
        except ValueError as e:
            raise ValueError(f"路径越界 wiki_dir: {path}") from e

    # ---- registry / redirects / outbox ----
    def get_registry(self) -> dict[str, dict]:
        if not self._registry_path.exists():
            return {}
        try:
            return cast(dict[str, dict], json.loads(self._registry_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            return {}

    def _write_registry(self, reg: dict) -> None:
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self._registry_path, reg)

    def get_redirects(self) -> dict[str, str]:
        if not self._redirects_path.exists():
            return {}
        try:
            return cast(dict[str, str], json.loads(self._redirects_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            return {}

    def _write_redirects(self, red: dict) -> None:
        self._redirects_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self._redirects_path, red)

    def _atomic_write_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".json.tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _append_outbox(self, event: dict) -> None:
        self._outbox_path.parent.mkdir(parents=True, exist_ok=True)
        with self._outbox_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_outbox(self) -> list[dict]:
        if not self._outbox_path.exists():
            return []
        return [json.loads(ln) for ln in self._outbox_path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    # ---- page CRUD ----
    def get_page(self, page_id: str) -> WikiPage | None:
        reg = self.get_registry()
        entry = reg.get(page_id)
        if not entry:
            return None
        path = self._wiki_dir / entry["path"]
        if not path.exists():
            return None
        return self._read_page_file(path)

    def get_page_by_title(self, title: str) -> WikiPage | None:
        reg = self.get_registry()
        slug = slugify(title)
        for pid, entry in reg.items():
            if entry["path"].endswith(f"/{slug}.md"):
                return self.get_page(pid)
        return None

    def list_pages(self, page_type: str | None = None) -> list[WikiPage]:
        pages: list[WikiPage] = []
        for entry in self.get_registry().values():
            if page_type and entry["page_type"] != page_type:
                continue
            path = self._wiki_dir / entry["path"]
            if path.exists():
                p = self._read_page_file(path)
                if p:
                    pages.append(p)
        return pages

    def list_claims(self) -> list[Claim]:
        """列出 claims/ 目录下所有非 RETRACTED claim(按文件名排序)。"""
        claims: list[Claim] = []
        claims_dir = self._wiki_dir / "claims"
        if not claims_dir.is_dir():
            return claims
        for yaml_path in sorted(claims_dir.glob("*.yaml")):
            c = self.get_claim(yaml_path.stem)  # get_claim 已过滤 RETRACTED + 校验失败
            if c is not None:
                claims.append(c)
        return claims

    def _read_page_file(self, path: Path) -> WikiPage | None:
        fm = read_frontmatter(path)
        if not fm.get("page_id"):
            return None
        text = path.read_text(encoding="utf-8")
        body = text.split("---", 2)[2].lstrip("\n") if "---" in text else ""
        fm["body"] = body
        try:
            return WikiPage.from_dict(fm, strict=False)
        except (ValueError, TypeError):
            return None

    def save_page(self, page: WikiPage, expected_revision: int | None = None) -> SaveResult:
        with self._lock:
            reg = self.get_registry()
            existing = reg.get(page.page_id)
            current_rev = existing["revision"] if existing else 0
            if expected_revision is not None and expected_revision != current_rev:
                raise StaleRevisionError(
                    f"page {page.page_id} expected_revision={expected_revision} 实际={current_rev}"
                )
            page.revision = current_rev + 1
            page.updated_at = page.updated_at or ""
            path = self._page_path(page.page_type, page.title)
            self._assert_inside_wiki(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            frontmatter = {k: v for k, v in page.to_dict().items() if k != "body"}
            write_markdown(path, frontmatter, page.body)
            rel = self._rel(path)
            reg[page.page_id] = PageRegistryEntry(
                path=rel, title=page.title, page_type=page.page_type.value,
                revision=page.revision, content_hash=page.content_hash,
            ).to_dict()
            self._write_registry(reg)
            event_type = "page.created" if current_rev == 0 else "page.updated"
            self._append_outbox({"type": event_type, "page_id": page.page_id, "revision": page.revision, "path": rel})
            return SaveResult(ok=True, object_id=page.page_id, revision=page.revision, outbox_events=[event_type])

    def move_page(self, page_id: str, new_title: str, new_page_type: str | None = None) -> SaveResult:
        with self._lock:
            page = self.get_page(page_id)
            if not page:
                raise KeyError(f"page not found: {page_id}")
            reg = self.get_registry()
            old_rel = reg[page_id]["path"]
            old_path = self._wiki_dir / old_rel
            page.title = new_title
            if new_page_type:
                page.page_type = PageType(new_page_type)
            r = self.save_page(page, expected_revision=page.revision)
            # save_page 已写新路径;删旧文件 + 记 redirect
            if (self._wiki_dir / old_rel) != self._page_path(page.page_type, new_title):
                with contextlib.suppress(FileNotFoundError):
                    old_path.unlink()
                red = self.get_redirects()
                red[old_rel] = page_id
                self._write_redirects(red)
            return r

    # ---- claim CRUD ----
    def get_claim(self, claim_id: str) -> Claim | None:
        path = self._claim_path(claim_id)
        if not path.exists():
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            claim = Claim.from_dict(data, strict=False)
            if claim.status == ClaimStatus.RETRACTED:
                return None
            return claim
        except (ValueError, TypeError):
            return None

    def save_claim(self, claim: Claim, expected_revision: int | None = None) -> SaveResult:
        with self._lock:
            existing = self.get_claim(claim.claim_id)
            current_rev = existing.revision if existing else 0
            if expected_revision is not None and expected_revision != current_rev:
                raise StaleRevisionError(
                    f"claim {claim.claim_id} expected_revision={expected_revision} 实际={current_rev}"
                )
            claim.revision = current_rev + 1
            claim.updated_at = claim.updated_at or ""
            path = self._claim_path(claim.claim_id)
            self._assert_inside_wiki(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    yaml.safe_dump(claim.to_dict(), f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            event_type = "claim.created" if current_rev == 0 else "claim.updated"
            self._append_outbox({"type": event_type, "claim_id": claim.claim_id, "revision": claim.revision})
            return SaveResult(ok=True, object_id=claim.claim_id, revision=claim.revision, outbox_events=[event_type])

    def delete_claim(self, claim_id: str, soft: bool = True) -> SaveResult:
        with self._lock:
            path = self._claim_path(claim_id)
            if not path.exists():
                return SaveResult(ok=False, object_id=claim_id, revision=0, warnings=["claim not found"])
            if soft:
                claim = self.get_claim(claim_id)
                if claim:
                    claim.status = ClaimStatus.RETRACTED
                    self.save_claim(claim, expected_revision=claim.revision)
            else:
                path.unlink()
            self._append_outbox({"type": "claim.deleted", "claim_id": claim_id, "soft": soft})
            return SaveResult(ok=True, object_id=claim_id, revision=0)

    # ---- transaction ----
    @contextlib.contextmanager
    def transaction(self) -> Iterator[WikiTransaction]:
        tx_id = uuid.uuid4().hex[:12]
        tx = WikiTransaction(self, tx_id)
        try:
            yield tx
        except BaseException:
            # 中途失败:丢弃 staging(本实现 stage 仅记录对象,commit 才落盘,故无残留)
            raise
        else:
            tx.commit()


def new_page_id() -> str:
    return f"page_{uuid.uuid4()}"


def new_claim_id() -> str:
    return f"claim_{uuid.uuid4()}"
