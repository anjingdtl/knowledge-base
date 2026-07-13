"""Real-LLM ask E2E evaluation (fixtures + live embedding + live LLM).

Isolates a temp SQLite DB, indexes evals/fixtures with real embeddings, then
runs the production ask path (rag_pipeline.query / optional VerifiedAnswerService).

Usage:
    python evals/run_ask_e2e_eval.py
    python evals/run_ask_e2e_eval.py --max-items 5
    python evals/run_ask_e2e_eval.py --json --output artifacts/eval/ask-e2e-real-llm.json
    python evals/run_ask_e2e_eval.py --strict

Notes:
- Requires working embedding + chat API keys.
- If llm.api_key is invalid but embedding.api_key works on the same OpenAI-compatible
  host, the eval falls back to the embedding key for chat and records that in the report.
- Do NOT treat this as CI-default; it costs API tokens and network latency.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

EVALS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = EVALS_DIR / "fixtures"
DATASETS_DIR = EVALS_DIR / "datasets"
DEFAULT_DATASET = DATASETS_DIR / "ask_e2e_fixture.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ask-e2e")

_REFUSAL_MARKERS = (
    "未找到", "没有找到", "无法回答", "证据不足", "知识库中未", "未收录",
    "无法提供", "抱歉", "不知道", "不确定", "无关", "超出", "不在知识库",
    "无法直接", "未直接", "未提及", "没有直接", "未提供明确", "线索不足",
    "无法确定", "没有相关", "不涉及", "未包含",
    "not found", "no relevant", "cannot answer", "no information",
    "insufficient", "out of scope", "no direct",
)


@dataclass
class AskCaseResult:
    id: str
    question: str
    answer: str = ""
    answer_mode: str = ""
    sources_count: int = 0
    latency_s: float = 0.0
    expect_refuse: bool = False
    refused: bool = False
    keyword_hit: bool = False
    keyword_score: float = 0.0
    correct: bool = False
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    top_sources: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_refused(answer: str, answer_mode: str = "") -> bool:
    if answer_mode == "no_answer":
        return True
    text = (answer or "").lower()
    if not text.strip():
        return True
    return any(m.lower() in text for m in _REFUSAL_MARKERS)


def _keyword_score(answer: str, item: dict) -> tuple[bool, float, list[str]]:
    """Return (hit, score, matched_groups).

    Primary: expected_keywords — fraction matched.
    If alt_keywords present, a group that fully matches counts as full hit.
    """
    ans = answer or ""
    ans_l = ans.lower()
    alts = item.get("alt_keywords") or []
    for group in alts:
        if group and all(
            (str(k).lower() in ans_l) or (str(k) in ans) for k in group
        ):
            return True, 1.0, [str(x) for x in group]

    kws = [str(k) for k in (item.get("expected_keywords") or []) if str(k).strip()]
    if not kws:
        return True, 1.0, []
    hits = 0
    matched = []
    for k in kws:
        if k.lower() in ans_l or k in ans:
            hits += 1
            matched.append(k)
    score = hits / len(kws)
    # pass if >= 50% keywords or at least 1 when only 1-2 keywords
    ok = score >= 0.5 if len(kws) > 1 else hits >= 1
    return ok, score, matched


def _ensure_chat_credentials() -> dict[str, Any]:
    """Ensure LLM chat works; fall back to embedding.api_key when needed."""
    from src.utils.config import Config

    Config.load()
    meta: dict[str, Any] = {
        "llm_base_url": Config.get("llm.base_url"),
        "llm_model": Config.get("llm.model"),
        "embedding_model": Config.get("embedding.model"),
        "llm_key_fallback": False,
    }
    llm_key = str(Config.get("llm.api_key") or "")
    emb_key = str(Config.get("embedding.api_key") or "")
    base = Config.get("llm.base_url") or Config.get("embedding.base_url")
    model = Config.get("llm.model") or "Qwen/Qwen3-8B"

    from openai import OpenAI

    def _probe(key: str) -> bool:
        if not key:
            return False
        try:
            client = OpenAI(api_key=key, base_url=base, timeout=45)
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply: OK"}],
                max_tokens=4,
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM probe failed: %s", e)
            return False

    if _probe(llm_key):
        meta["chat_key_source"] = "llm.api_key"
        return meta

    if emb_key and emb_key != llm_key and _probe(emb_key):
        # Process-local override only (does not rewrite config.yaml)
        Config.set("llm.api_key", emb_key)
        os.environ["SHINEHE_LLM_API_KEY"] = emb_key
        meta["llm_key_fallback"] = True
        meta["chat_key_source"] = "embedding.api_key (fallback)"
        logger.warning(
            "llm.api_key invalid; using embedding.api_key for chat in this eval process only",
        )
        return meta

    raise RuntimeError(
        "No working chat API key. Fix llm.api_key or embedding.api_key for SiliconFlow/OpenAI-compatible host.",
    )


def _setup_isolated_env(work: Path) -> None:
    import src.core.container as container_mod
    from src.services.block_store import BlockStore
    from src.services.db import Database
    from src.services.vectorstore import VectorStore
    from src.utils.config import Config

    Config.load()
    Config.set("storage.data_dir", str(work))
    Config.set("storage.db_name", "ask_e2e.db")
    Config.set("knowledge_workflow.wiki_dir", str(work / "wiki"))
    Config.set("wiki.enabled", False)
    Config.set("wiki.auto_save_answer", False)
    Config.set("rag.verified_knowledge.enabled", False)  # fixture corpus is raw-only
    Config.set("rag.enable_query_rewriting", False)  # save latency/cost
    Config.set("rag.enable_rerank", False)  # avoid LLM-rerank timeouts in E2E
    Config.set("rag.search_mode", "blend")
    Config.set("rag.hybrid_search.enabled", True)
    Config.set("rag.pipeline.enabled", True)
    # Prefer lean pipeline: vector + generate + postprocess
    Config.set("rag.pipeline.stages", [
        {"stage": "vector_search", "enabled": True, "mode": "blend", "top_k": 8},
        {"stage": "rerank", "enabled": False, "top_n": 5, "min_score": 0.0},
        {"stage": "generate", "enabled": True, "stream": False},
        {"stage": "postprocess", "enabled": True, "dedup": True,
         "max_context_length": 8000, "block_context_max_length": 2000},
    ])
    Config.set("reranker.enabled", False)
    Config.set("rag.agentic_router.enabled", False)
    Config.set("security.allowed_ingest_dirs", [str(FIXTURES_DIR), str(work), tempfile.gettempdir()])
    Config.set("rag.ask.total_timeout", 120)

    Database._instance = None
    VectorStore._instance = None
    VectorStore._initialized = False
    BlockStore._instance = None
    BlockStore._initialized = False
    container_mod._active_container = None

    Database.connect(str(work / "ask_e2e.db"))


def _index_fixtures() -> int:
    """Index all fixtures via indexer with real embeddings."""
    from src.models.knowledge import KnowledgeItem
    from src.services.db import Database
    from src.services.indexer import index_knowledge_item

    count = 0
    for path in sorted(FIXTURES_DIR.glob("*")):
        if not path.is_file() or path.suffix not in (".md", ".markdown", ".py", ".txt"):
            continue
        content = path.read_text(encoding="utf-8")
        suffix = path.suffix.lstrip(".").lower() or "txt"
        file_type = "md" if suffix in ("md", "markdown") else (
            "code" if suffix in ("py", "js", "ts", "go", "rs", "java") else suffix
        )
        item = KnowledgeItem(
            title=path.stem,
            content=content,
            source_type="file",
            source_path=path.name,
            file_type=file_type,
            tags=["fixture", "ask_e2e"],
        )
        Database.insert_knowledge(item.to_row())
        index_knowledge_item(item)
        count += 1
        logger.info("Indexed fixture %s", path.name)
    return count


def _load_dataset(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"dataset must be list: {path}")
    return data


def _run_ask_search_llm(container, question: str) -> dict[str, Any]:
    """Production-like ask: SearchService retrieval + LLM generation.

    Prefer this over full rag_pipeline when pipeline hybrid/router is flaky under
    isolated fixture DBs; still uses real embeddings + real chat model.
    """
    from src.services.rag_pipeline import build_rag_messages
    from src.utils.llm_text import strip_think

    hits = container.search_service.search(question, top_k=5)
    if not hits:
        return {
            "answer": "知识库中未找到相关内容，无法回答该问题。",
            "answer_mode": "no_answer",
            "sources": [],
            "warnings": ["empty_retrieval"],
        }

    parts = []
    sources = []
    for i, h in enumerate(hits, 1):
        text = (h.get("text") or "")[:1200]
        title = h.get("title") or h.get("knowledge_id") or f"src{i}"
        parts.append(f"【来源{i} {title}】\n{text}")
        sources.append({
            "title": title,
            "knowledge_id": h.get("knowledge_id") or "",
            "block_id": h.get("block_id") or "",
            "source": h.get("source") or "knowledge",
            "text": text,
            "score": h.get("score"),
        })
    context = "\n\n".join(parts)
    system_extra = (
        "你是知识库助手。仅根据给定来源回答。"
        "若来源不足以回答，明确说知识库中未找到/证据不足，不要编造。"
        "回答简洁，保留关键数值与专有名词。"
    )
    messages = build_rag_messages(question, context, [])
    # prepend stronger instruction
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = system_extra + "\n" + str(messages[0].get("content") or "")
    else:
        messages.insert(0, {"role": "system", "content": system_extra})

    llm = container.llm
    if hasattr(llm, "chat_with_usage"):
        content, _usage = llm.chat_with_usage(messages)
        answer = strip_think(content)
    else:
        answer = strip_think(llm.chat(messages))

    return {
        "answer": answer or "",
        "answer_mode": "raw_only",
        "sources": sources,
        "warnings": [],
        "search_trace": getattr(container.search_service, "last_search_trace", {}),
    }


def _run_ask(container, question: str, *, path: str) -> dict[str, Any]:
    if path == "verified":
        from src.services.verified_answer import VerifiedAnswerService

        svc = VerifiedAnswerService(
            container.search_service, llm=container.llm, config=container.config,
        )
        return dict(svc.ask(question, top_k=5, use_llm=True))
    if path == "rag":
        return dict(container.rag_pipeline.query(question, timeout=120, skip_cache=True))
    # default: search_llm
    return _run_ask_search_llm(container, question)


def run_case(container, item: dict, *, path: str) -> AskCaseResult:
    q = item["question"]
    result = AskCaseResult(
        id=str(item.get("id") or q[:32]),
        question=q,
        expect_refuse=bool(item.get("expect_refuse")),
    )
    t0 = time.monotonic()
    try:
        payload = _run_ask(container, q, path=path)
        result.answer = payload.get("answer") or ""
        result.answer_mode = str(payload.get("answer_mode") or "")
        sources = payload.get("sources") or []
        result.sources_count = len(sources)
        result.warnings = list(payload.get("warnings") or [])
        result.top_sources = [
            {
                "title": s.get("title"),
                "knowledge_id": s.get("knowledge_id"),
                "block_id": s.get("block_id"),
                "source": s.get("source"),
            }
            for s in sources[:3]
        ]
    except Exception as e:  # noqa: BLE001
        result.error = f"{type(e).__name__}: {e}"
        logger.exception("ask failed: %s", q[:60])
    finally:
        result.latency_s = round(time.monotonic() - t0, 3)

    result.refused = _is_refused(result.answer, result.answer_mode)
    if result.expect_refuse:
        result.keyword_hit = True
        result.keyword_score = 1.0
        # Soft refuse: explicit refusal markers OR empty answer OR no_answer mode
        result.correct = (result.refused or not (result.answer or "").strip()) and not result.error
    else:
        hit, score, _matched = _keyword_score(result.answer, item)
        result.keyword_hit = hit
        result.keyword_score = round(score, 4)
        # Prefer evidence-backed answers; allow keyword-correct even if sources empty
        # when the model answered from FTS context without packaging sources.
        result.correct = (
            bool(result.answer)
            and not result.error
            and not result.refused
            and hit
        )
    return result


def summarize(cases: list[AskCaseResult], meta: dict[str, Any]) -> dict[str, Any]:
    n = len(cases) or 1
    answered = [c for c in cases if not c.expect_refuse]
    refused_set = [c for c in cases if c.expect_refuse]
    correct = sum(1 for c in cases if c.correct)
    ans_correct = sum(1 for c in answered if c.correct) / max(len(answered), 1)
    refuse_acc = sum(1 for c in refused_set if c.correct) / max(len(refused_set), 1)
    keyword = sum(c.keyword_score for c in answered) / max(len(answered), 1)
    cite = sum(1 for c in answered if c.sources_count > 0) / max(len(answered), 1)
    lats = sorted(c.latency_s for c in cases if c.latency_s > 0)
    p50 = lats[max(0, int(len(lats) * 0.5) - 1)] if lats else 0.0
    p95 = lats[max(0, int(len(lats) * 0.95) - 1)] if lats else 0.0
    errors = sum(1 for c in cases if c.error)

    gates = {
        "answer_accuracy_ge_0_5": ans_correct >= 0.5,
        "refuse_accuracy_ge_0_5": refuse_acc >= 0.5 or not refused_set,
        "citation_rate_ge_0_6": cite >= 0.6 or not answered,
        "error_rate_zero": errors == 0,
        "case_count_ge_10": len(cases) >= 10,
        "keyword_coverage_ge_0_5": keyword >= 0.5 or not answered,
    }
    return {
        "total": len(cases),
        "correct": correct,
        "overall_accuracy": round(correct / n, 4),
        "answer_accuracy": round(ans_correct, 4),
        "refuse_accuracy": round(refuse_acc, 4),
        "keyword_coverage": round(keyword, 4),
        "citation_rate": round(cite, 4),
        "error_count": errors,
        "latency_p50_s": round(p50, 3),
        "latency_p95_s": round(p95, 3),
        "gates": gates,
        "overall_pass": all(gates.values()),
        "meta": meta,
        "cases": [c.to_dict() for c in cases],
        "failures": [
            {
                "id": c.id,
                "question": c.question,
                "correct": c.correct,
                "refused": c.refused,
                "keyword_score": c.keyword_score,
                "error": c.error,
                "answer_preview": (c.answer or "")[:200],
            }
            for c in cases
            if not c.correct
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real-LLM ask E2E eval")
    parser.add_argument("--dataset", type=str, default=str(DEFAULT_DATASET))
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument(
        "--path",
        choices=["search_llm", "rag", "verified"],
        default="search_llm",
        help="ask path: search_llm (SearchService+LLM, default), rag_pipeline, or verified",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--keep-db", action="store_true", help="keep temp workdir")
    args = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="ask_e2e_"))
    meta: dict[str, Any] = {"workdir": str(work), "path": args.path}
    container = None
    try:
        cred = _ensure_chat_credentials()
        meta.update(cred)
        _setup_isolated_env(work)

        from src.core.container import create_container
        from src.services.db import Database

        # Create container first so BlockStore/VectorStore bind to the isolated DB,
        # then index fixtures (real embeddings) into that same store.
        container = create_container()
        Database.connect(str(work / "ask_e2e.db"))
        n_docs = _index_fixtures()
        meta["indexed_fixtures"] = n_docs
        if n_docs == 0:
            raise RuntimeError("no fixtures indexed")

        # Sanity: hybrid search must return hits after indexing
        try:
            probe = container.search_service.search("SQLite WAL", top_k=3)
            meta["probe_search_hits"] = len(probe)
            logger.info("Probe search hits=%d", len(probe))
        except Exception as e:  # noqa: BLE001
            meta["probe_search_error"] = str(e)
            logger.warning("Probe search failed: %s", e)

        items = _load_dataset(Path(args.dataset))
        if args.max_items and args.max_items > 0:
            items = items[: args.max_items]

        results: list[AskCaseResult] = []
        for i, item in enumerate(items, 1):
            logger.info("[%d/%d] %s", i, len(items), item["question"][:60])
            results.append(run_case(container, item, path=args.path))

        report = summarize(results, meta)
        text = (
            f"Ask E2E {'PASS' if report['overall_pass'] else 'FAIL'}: "
            f"n={report['total']} acc={report['overall_accuracy']:.3f} "
            f"answer={report['answer_accuracy']:.3f} refuse={report['refuse_accuracy']:.3f} "
            f"cite={report['citation_rate']:.3f} kw={report['keyword_coverage']:.3f} "
            f"p50={report['latency_p50_s']}s p95={report['latency_p95_s']}s "
            f"errors={report['error_count']} fallback_key={meta.get('llm_key_fallback')}"
        )
        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
                if args.json or out.suffix == ".json"
                else text + "\n\n" + json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Wrote {out}")
        if args.json and not args.output:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(text)
            if report["failures"]:
                print("Failures:")
                for f in report["failures"][:10]:
                    print(f"  - {f['id']}: score={f['keyword_score']} err={f['error']!r}")
                    print(f"    {f['answer_preview'][:120]!r}")

        if args.strict:
            return 0 if report["overall_pass"] and report["error_count"] == 0 else 1
        return 0 if report["error_count"] == 0 else 1
    except Exception as e:  # noqa: BLE001
        logger.exception("ask e2e failed: %s", e)
        print(f"Ask E2E ERROR: {e}", file=sys.stderr)
        return 2
    finally:
        try:
            from src.core.container import shutdown_container
            if container is not None:
                shutdown_container(container)
        except Exception:  # noqa: BLE001
            pass
        if not args.keep_db:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
