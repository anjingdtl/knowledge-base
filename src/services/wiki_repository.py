"""Canonical Wiki filesystem Repository(spec §4.2 ADR-002 / §14.1 / C3)。

唯一 canonical 写入口:页面 Markdown + Claim YAML + registry + redirects + outbox。
- 原子写:复用 wiki_slug.write_markdown(tempfile + os.replace);Claim YAML 同模式
- revision 乐观锁:save 时比对 expected_revision,失配抛 StaleRevisionError
- transaction(C3 严格 staging):stage 物理写 _staging/<tx_id>/ → validate →
  manifest → publish(os.replace)→ write registry(原子)→ COMMITTED marker →
  append outbox(带 tx_id)→ cleanup。中途崩溃由 recover() 根据 COMMITTED/manifest
  恢复(前向补 outbox 或丢弃孤儿)。
- recover:启动/每次 transaction 进入前扫描 _staging/,完成未收尾的事务。
- 路径安全:所有 canonical 路径必须在 wiki_dir 内,禁 ..
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
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


class TransactionValidationError(RuntimeError):
    """transaction commit 前 validate 失败(对象 invariant 不满足)。"""


class RegistryCorruptError(RuntimeError):
    """pages.json registry 损坏；拒绝用空 dict 继续写以免覆盖丢页。"""


def new_page_id() -> str:
    return f"page_{uuid.uuid4()}"


def new_claim_id() -> str:
    return f"claim_{uuid.uuid4()}"


class WikiTransaction:
    """严格 staging 事务(spec §14.1 / C3)。

    stage_page/stage_claim 记录意图;commit 时:
    1. validate 全部 staged 对象
    2. finalize revisions(乐观锁检查 expected_revision)
    3. 物理写 _staging/<tx_id>/ 下的 page/claim 文件
    4. write manifest.json(对象清单 + revisions)
    5. publish:逐个 os.replace staging→canonical + 收集 registry 变更
    6. write registry(一次性原子写)
    7. write COMMITTED marker
    8. append outbox events(带 tx_id,供幂等补写)
    9. cleanup staging 目录

    中途异常 → 不写 COMMITTED → 下次 recover() 丢弃孤儿(未写 registry 的 canonical
    文件不被 list_pages 返回,不污染查询)。
    """

    def __init__(self, repo: "WikiRepository", tx_id: str):
        self._repo = repo
        self._tx_id = tx_id
        self._staging_dir = repo._staging_dir / tx_id
        self._staged_pages: list[tuple[WikiPage, int | None]] = []
        self._staged_claims: list[tuple[Claim, int | None]] = []
        self._committed = False

    @property
    def tx_id(self) -> str:
        return self._tx_id

    def stage_page(self, page: WikiPage, expected_revision: int | None = None) -> None:
        self._staged_pages.append((page, expected_revision))

    def stage_claim(self, claim: Claim, expected_revision: int | None = None) -> None:
        self._staged_claims.append((claim, expected_revision))

    def commit(self) -> list[SaveResult]:
        if self._committed:
            raise RuntimeError(f"transaction {self._tx_id} already committed")
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        results: list[SaveResult] = []

        # 1. validate 全部对象(含跨对象 invariant,如 supersedes 的 old+new 都要过)
        for page, _ in self._staged_pages:
            errors = page.validate()
            if errors:
                raise TransactionValidationError(f"page {page.page_id}: {errors}")
        for claim, _ in self._staged_claims:
            errors = claim.validate()
            if errors:
                raise TransactionValidationError(f"claim {claim.claim_id}: {errors}")

        # 2-4. finalize revision + 物理写 staging + manifest
        with self._repo._lock:  # 原子性:finalize 到 cleanup 全程持锁
            reg = self._repo.get_registry()
            manifest: dict = {"tx_id": self._tx_id, "pages": [], "claims": []}
            staged_files: list[tuple[str, Path, Path, dict]] = []  # (kind, staging, canonical, reg_entry)

            for page, exp in self._staged_pages:
                existing = reg.get(page.page_id)
                current_rev = existing["revision"] if existing else 0
                if exp is not None and exp != current_rev:
                    raise StaleRevisionError(
                        f"page {page.page_id} expected_revision={exp} 实际={current_rev}"
                    )
                page.revision = current_rev + 1
                page.updated_at = page.updated_at or ""
                canonical = self._repo._page_path(page.page_type, page.title)
                self._repo._assert_inside_wiki(canonical)
                canonical.parent.mkdir(parents=True, exist_ok=True)
                staging = self._staging_dir / f"page_{page.page_id}.md"
                self._repo._write_page_file(staging, page)
                rel = self._repo._rel(canonical)
                reg_entry = PageRegistryEntry(
                    path=rel, title=page.title, page_type=page.page_type.value,
                    revision=page.revision, content_hash=page.content_hash,
                ).to_dict()
                event_type = "page.created" if current_rev == 0 else "page.updated"
                staged_files.append(("page", staging, canonical, reg_entry))
                manifest["pages"].append({"page_id": page.page_id, "revision": page.revision,
                                          "event": event_type, "path": rel})

            for claim, exp in self._staged_claims:
                existing_claim = self._repo._read_claim_raw(claim.claim_id)
                current_rev = existing_claim.revision if existing_claim else 0
                if exp is not None and exp != current_rev:
                    raise StaleRevisionError(
                        f"claim {claim.claim_id} expected_revision={exp} 实际={current_rev}"
                    )
                claim.revision = current_rev + 1
                claim.updated_at = claim.updated_at or ""
                canonical = self._repo._claim_path(claim.claim_id)
                self._repo._assert_inside_wiki(canonical)
                canonical.parent.mkdir(parents=True, exist_ok=True)
                staging = self._staging_dir / f"claim_{claim.claim_id}.yaml"
                self._repo._write_claim_file(staging, claim)
                # RETRACTED 软删：投影侧以 claim.deleted 收敛（get_claim 已不可见）
                if claim.status == ClaimStatus.RETRACTED:
                    event_type = "claim.deleted"
                else:
                    event_type = "claim.created" if current_rev == 0 else "claim.updated"
                staged_files.append(("claim", staging, canonical, {}))
                manifest["claims"].append({"claim_id": claim.claim_id, "revision": claim.revision,
                                           "event": event_type})
                results.append(SaveResult(ok=True, object_id=claim.claim_id,
                                          revision=claim.revision, outbox_events=[event_type]))

            # manifest 落盘(step 4)
            (self._staging_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )

            # 5. publish:逐个 os.replace staging→canonical + 更新 reg
            published_page_results: list[SaveResult] = []
            idx_page = 0
            for kind, staging, canonical, reg_entry in staged_files:
                os.replace(staging, canonical)  # 原子
                if kind == "page":
                    pid = self._staged_pages[idx_page][0].page_id
                    reg[pid] = reg_entry
                    ev = manifest["pages"][idx_page]
                    published_page_results.append(SaveResult(
                        ok=True, object_id=pid, revision=ev["revision"], outbox_events=[ev["event"]]))
                    idx_page += 1
                # claim reg_entry 为空(claims 无 registry)

            # 6. write registry(原子)—— registry 最后写:写成功则查询可见,否则孤儿
            self._repo._write_registry(reg)

            # 7. COMMITTED marker
            (self._staging_dir / "COMMITTED").write_text("ok", encoding="utf-8")

            # 8. append outbox events(带 tx_id,供 recover 幂等补写)
            for m in manifest["pages"]:
                self._repo._append_outbox({"type": m["event"], "page_id": m["page_id"],
                                           "revision": m["revision"], "path": m["path"], "tx_id": self._tx_id})
            for m in manifest["claims"]:
                self._repo._append_outbox({"type": m["event"], "claim_id": m["claim_id"],
                                           "revision": m["revision"], "tx_id": self._tx_id})

            self._committed = True

        # 9. cleanup
        with contextlib.suppress(OSError):
            shutil.rmtree(self._staging_dir)

        # 合并 results:pages 在前,claims 在后(与 stage 顺序一致)
        return published_page_results + results


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
        self._staging_dir = self._wiki_dir / "_staging"
        self._staging_dir.mkdir(exist_ok=True)
        # 启动即恢复一次(扫残留事务)
        self.recover()

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

    # ---- 物理写文件(抽取,save 与 staging 共用)----
    def _write_page_file(self, path: Path, page: WikiPage) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = {k: v for k, v in page.to_dict().items() if k != "body"}
        write_markdown(path, frontmatter, page.body)

    def _write_claim_file(self, path: Path, claim: Claim) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(claim.to_dict(), f, allow_unicode=True,
                               default_flow_style=False, sort_keys=False)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # ---- registry / redirects / outbox ----
    def get_registry(self) -> dict[str, dict]:
        if not self._registry_path.exists():
            return {}
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RegistryCorruptError(
                f"registry corrupt at {self._registry_path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise RegistryCorruptError(
                f"registry corrupt at {self._registry_path}: expected object, got {type(data).__name__}"
            )
        return cast(dict[str, dict], data)

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

    def _outbox_tx_ids(self) -> set[str]:
        """已写入 outbox 的 tx_id 集合(观测/兼容用)。"""
        ids: set[str] = set()
        for ev in self.read_outbox():
            tx = ev.get("tx_id")
            if tx:
                ids.add(tx)
        return ids

    def _outbox_event_keys(self) -> set[tuple]:
        """已写入 outbox 的 (tx_id, type, object_id) 集合 — recover 按事件幂等补写。"""
        keys: set[tuple] = set()
        for ev in self.read_outbox():
            tx = ev.get("tx_id")
            etype = ev.get("type")
            if not tx or not etype:
                continue
            obj = ev.get("page_id") or ev.get("claim_id") or ""
            keys.add((tx, etype, obj))
        return keys

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
            c = self.get_claim(yaml_path.stem)
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
        """单对象原子写(非事务)。多对象原子请用 transaction()。"""
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
            self._write_page_file(path, page)
            rel = self._rel(path)
            reg[page.page_id] = PageRegistryEntry(
                path=rel, title=page.title, page_type=page.page_type.value,
                revision=page.revision, content_hash=page.content_hash,
            ).to_dict()
            self._write_registry(reg)
            event_type = "page.created" if current_rev == 0 else "page.updated"
            self._append_outbox({"type": event_type, "page_id": page.page_id,
                                 "revision": page.revision, "path": rel, "tx_id": f"solo_{uuid.uuid4().hex[:8]}"})
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
            if (self._wiki_dir / old_rel) != self._page_path(page.page_type, new_title):
                with contextlib.suppress(FileNotFoundError):
                    old_path.unlink()
                red = self.get_redirects()
                red[old_rel] = page_id
                self._write_redirects(red)
            return r

    # ---- claim CRUD ----
    def _read_claim_raw(self, claim_id: str) -> Claim | None:
        """读 claim 原始对象(不过滤 RETRACTED,事务 revision 计算用)。"""
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
            return Claim.from_dict(data, strict=False)
        except (ValueError, TypeError):
            return None

    def get_claim(self, claim_id: str) -> Claim | None:
        claim = self._read_claim_raw(claim_id)
        if claim is None:
            return None
        if claim.status == ClaimStatus.RETRACTED:
            return None
        return claim

    def save_claim(self, claim: Claim, expected_revision: int | None = None) -> SaveResult:
        """单对象原子写(非事务)。"""
        with self._lock:
            existing = self._read_claim_raw(claim.claim_id)
            current_rev = existing.revision if existing else 0
            if expected_revision is not None and expected_revision != current_rev:
                raise StaleRevisionError(
                    f"claim {claim.claim_id} expected_revision={expected_revision} 实际={current_rev}"
                )
            claim.revision = current_rev + 1
            claim.updated_at = claim.updated_at or ""
            path = self._claim_path(claim.claim_id)
            self._assert_inside_wiki(path)
            self._write_claim_file(path, claim)
            if claim.status == ClaimStatus.RETRACTED:
                event_type = "claim.deleted"
            else:
                event_type = "claim.created" if current_rev == 0 else "claim.updated"
            self._append_outbox({"type": event_type, "claim_id": claim.claim_id,
                                 "revision": claim.revision, "tx_id": f"solo_{uuid.uuid4().hex[:8]}"})
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
            self._append_outbox({"type": "claim.deleted", "claim_id": claim_id, "soft": soft,
                                 "tx_id": f"solo_{uuid.uuid4().hex[:8]}"})
            return SaveResult(ok=True, object_id=claim_id, revision=0)

    # ---- Serving API (Phase 2: read-only, no staging, no writes) ----

    def _default_serving_gate(self, gate: object | None = None):
        """Build or reuse WikiServingGate with DB lookups when available."""
        if gate is not None:
            return gate
        from src.services.wiki_serving_gate import (
            WikiServingGate,
            default_block_knowledge_lookups,
        )

        get_block, get_knowledge = default_block_knowledge_lookups()
        return WikiServingGate(get_block=get_block, get_knowledge=get_knowledge)

    def list_servable_claims(
        self,
        *,
        gate: object | None = None,
        include_disclose: bool = False,
        limit: int | None = None,
    ) -> list[Claim]:
        """List claims eligible as primary (optionally disclose-only) conclusions.

        Does **not** return draft / stale / unsupported / retracted as primary.
        Does **not** read staging. No write side effects.
        """
        g = self._default_serving_gate(gate)
        pairs = g.filter_servable(
            self.list_claims(),
            include_disclose=include_disclose,
            limit=limit,
        )
        return [c for c, _ in pairs]

    def get_servable_claim(
        self,
        claim_id: str,
        *,
        gate: object | None = None,
        include_disclose: bool = False,
    ) -> Claim | None:
        """Return claim only if it passes the Serving Gate."""
        claim = self.get_claim(claim_id)
        if claim is None:
            return None
        g = self._default_serving_gate(gate)
        decision = g.evaluate(claim)
        if decision.eligible:
            return claim
        if include_disclose and decision.disclose_only:
            return claim
        return None

    def resolve_claim_evidence(
        self,
        claim: Claim | str,
        *,
        gate: object | None = None,
    ) -> list:
        """Resolve supports evidence for a claim (block + hash + knowledge).

        Read-only; returns ResolvedEvidence list from the gate.
        """
        if isinstance(claim, str):
            obj = self.get_claim(claim)
            if obj is None:
                return []
            claim = obj
        g = self._default_serving_gate(gate)
        return g.resolve_claim_evidence(claim)

    def get_claim_serving_diagnostics(
        self,
        *,
        gate: object | None = None,
    ) -> dict:
        """Aggregate serving diagnostics for Doctor / health (no writes)."""
        g = self._default_serving_gate(gate)
        return g.diagnostics_for_claims(self.list_claims())

    # ---- transaction(C3 严格 staging)----
    @contextlib.contextmanager
    def transaction(self) -> Iterator[WikiTransaction]:
        """严格 staging 事务。进入时先 recover() 清理残留,异常自动丢弃 staging。"""
        self.recover()
        tx_id = f"tx_{uuid.uuid4().hex[:12]}"
        tx = WikiTransaction(self, tx_id)
        try:
            yield tx
        except BaseException:
            # 中途异常:丢弃未提交 staging(未写 COMMITTED → recover 会清理;此处主动清)
            with contextlib.suppress(OSError):
                if tx._staging_dir.exists():
                    shutil.rmtree(tx._staging_dir)
            raise
        else:
            if not tx._committed:
                tx.commit()

    # ---- 崩溃恢复(C3)----
    def recover(self) -> list[str]:
        """扫描 _staging/ 下未收尾的事务,按 COMMITTED/manifest 恢复。

        - 有 COMMITTED + manifest:tx 已 publish+registry,补 outbox(幂等)+ cleanup。
        - 有 manifest 无 COMMITTED:publish 或 registry 中断。
            * registry 已含 manifest 对象 → 视为前向完成,补 outbox + cleanup。
            * registry 不含 → 孤儿 canonical(list_pages 基于 registry 忽略),cleanup。
        - 无 manifest:stage 中断,cleanup。
        返回已恢复的 tx_id 列表(供观测/测试)。
        """
        recovered: list[str] = []
        if not self._staging_dir.exists():
            return recovered
        with self._lock:
            existing_keys = self._outbox_event_keys()
            for tx_dir in sorted(self._staging_dir.iterdir()):
                if not tx_dir.is_dir():
                    continue
                tx_id = tx_dir.name
                manifest_path = tx_dir / "manifest.json"
                committed = (tx_dir / "COMMITTED").exists()
                if not manifest_path.exists():
                    # stage 中断(manifest 未写)→ 安全丢弃
                    with contextlib.suppress(OSError):
                        shutil.rmtree(tx_dir)
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    with contextlib.suppress(OSError):
                        shutil.rmtree(tx_dir)
                    continue

                # 判定是否前向完成(registry 已写 / claim 文件已对齐 revision / COMMITTED)
                try:
                    reg = self.get_registry()
                except RegistryCorruptError:
                    reg = {}
                page_entries = manifest.get("pages", [])
                claim_entries = manifest.get("claims", [])
                if page_entries:
                    reg_written = all(
                        reg.get(p["page_id"], {}).get("revision") == p["revision"]
                        for p in page_entries
                    )
                elif claim_entries:
                    # claim-only: COMMITTED 或磁盘 claim revision 已与 manifest 对齐
                    reg_written = committed or all(
                        self._claim_revision_matches(c["claim_id"], c["revision"])
                        for c in claim_entries
                    )
                else:
                    reg_written = committed
                if committed or reg_written:
                    # 前向完成:按 (tx_id, type, object_id) 幂等补写缺失事件
                    for p in page_entries:
                        key = (tx_id, p["event"], p["page_id"])
                        if key not in existing_keys:
                            self._append_outbox({
                                "type": p["event"], "page_id": p["page_id"],
                                "revision": p["revision"], "path": p["path"], "tx_id": tx_id,
                            })
                            existing_keys.add(key)
                    for c in claim_entries:
                        key = (tx_id, c["event"], c["claim_id"])
                        if key not in existing_keys:
                            self._append_outbox({
                                "type": c["event"], "claim_id": c["claim_id"],
                                "revision": c["revision"], "tx_id": tx_id,
                            })
                            existing_keys.add(key)
                    recovered.append(tx_id)
                # 无论前向还是孤儿,canonical 文件已是 os.replace 后的完整状态;
                # 孤儿(registry 未含)不污染查询(list_pages 基于 registry),保留文件供人工排查或后续重放
                with contextlib.suppress(OSError):
                    shutil.rmtree(tx_dir)
        return recovered

    def _claim_revision_matches(self, claim_id: str, revision: int) -> bool:
        raw = self._read_claim_raw(claim_id)
        return raw is not None and raw.revision == revision
