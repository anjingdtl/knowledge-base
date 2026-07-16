"""Production-pilot real MCP harness (formal DB read-only + real providers).

Runs Phases 5–8 sampling against stdio and/or streamable-http.
Never enables write policy against formal DB.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.production_pilot_metrics import (  # noqa: E402
    metrics_to_jsonable,
    score_answer_citations,
    score_no_answer,
    score_numeric_units,
    score_retrieval,
    score_routing,
)
from scripts.final_closure_mcp_harness import (  # noqa: E402
    HttpMcpServer,
    call_tool,
    list_tools,
)

ART = ROOT / "artifacts" / "production-pilot-final-validation"
DATA = ROOT / "tests" / "eval" / "datasets" / "frozen"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def formal_home() -> Path:
    """Temp SHINEHE_HOME pointing storage at formal absolute data dir."""
    home = Path(tempfile.mkdtemp(prefix="pilot_formal_"))
    cfg = {
        "storage": {
            "data_dir": str((ROOT / "data").resolve()),
            "db_name": "kb.db",
            "graph_dir": str((ROOT / "data" / "graph").resolve()),
        },
        "wiki": {"enabled": False, "auto_compile": False},
        "rag": {
            "enable_query_rewriting": False,
            "enable_rerank": False,
            "ask": {"total_timeout": 45, "no_answer_threshold": 0.35},
            "search": {"no_match_threshold": 0.35},
            "max_graph_nodes": 200,
            "use_planetary_router": True,
            "route_llm_timeout": 8,
            "search_mode": "hybrid",
        },
        "mcp": {
            "tool_profile": "full",
            "experimental_tools_enabled": True,
            "enable_legacy_aliases": False,
            "allow_http_write": False,
            "write_policy": "deny",
        },
        "embedding": {
            "base_url": "https://api.siliconflow.cn/v1",
            "model": "BAAI/bge-m3",
            "timeout": 30,
            "dimension": 1024,
        },
        "llm": {
            "base_url": "https://api.minimaxi.com/v1",
            "model": "MiniMax-M3",
            "timeout": 45,
        },
        "reranker": {
            "model": "BAAI/bge-reranker-v2-m3",
            "timeout": 30,
        },
    }
    (home / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return home


def _payload_rows(payload: Any) -> list[dict]:
    """Normalize MCP envelope to list of hit dicts."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "hits", "sources", "documents", "rows"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
        return [data]
    for key in ("results", "items", "hits", "sources"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def _extract_ids(payload: Any) -> list[str]:
    ids: list[str] = []
    for r in _payload_rows(payload):
        kid = (
            r.get("knowledge_id")
            or r.get("page_id")
            or r.get("source_id")
            or r.get("id")
        )
        if kid:
            ids.append(str(kid))
    return ids


def _texts(payload: Any) -> list[str]:
    texts: list[str] = []
    for r in _payload_rows(payload):
        t = r.get("text") or r.get("content") or r.get("snippet") or r.get("title") or ""
        texts.append(str(t))
    return texts


def _no_match(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if meta.get("no_match") or payload.get("no_match"):
        return True
    data = payload.get("data")
    if isinstance(data, dict) and (
        data.get("no_match") or data.get("answer_mode") == "no_answer"
    ):
        return True
    if isinstance(data, list) and len(data) == 0 and meta.get("no_match"):
        return True
    return False


TASK_SUCCESS_OUTCOMES = {"non_empty", "no_answer", "graph_result", "structured_result"}


def _payload_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload


def _string_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values.append(value)
    elif isinstance(value, dict):
        for nested in value.values():
            values.extend(_string_values(nested))
    elif isinstance(value, list):
        for nested in value:
            values.extend(_string_values(nested))
    return values


def _has_timeout_signal(payload: Any, error_code: Any = None) -> bool:
    if "timeout" in str(error_code or "").lower():
        return True
    if not isinstance(payload, dict):
        return False
    if payload.get("timeout") is True or payload.get("timed_out") is True:
        return True
    if "timeout" in str(payload.get("error_code") or "").lower():
        return True
    meta = payload.get("meta")
    if isinstance(meta, dict) and (
        meta.get("timeout") is True or meta.get("timed_out") is True
    ):
        return True
    data = _payload_data(payload)
    if isinstance(data, dict):
        if str(data.get("answer_mode") or "").lower() == "timeout":
            return True
        route = data.get("route")
        if isinstance(route, dict) and str(route.get("mode") or "").lower() == "timeout":
            return True
        if str(data.get("mode") or "").lower() == "timeout":
            return True
        inner_meta = data.get("meta")
        if isinstance(inner_meta, dict) and inner_meta.get("timeout") is True:
            return True
    return any("timeout" in value.lower() for value in _string_values(payload.get("warnings")))


def _error_outcome(payload: Any, error_code: Any = None) -> str | None:
    codes = [str(error_code or "")]
    if isinstance(payload, dict):
        codes.extend(
            str(payload.get(key) or "") for key in ("error_code", "code", "error", "type")
        )
        data = _payload_data(payload)
        if isinstance(data, dict):
            codes.extend(str(data.get(key) or "") for key in ("error_code", "code", "error"))
    joined = " ".join(codes).lower()
    if "timeout" in joined:
        return "timeout"
    if "validation" in joined or "invalid_argument" in joined:
        return "validation_error"
    if "provider" in joined:
        return "provider_error"
    if "transport" in joined or "connection" in joined:
        return "transport_error"
    if "mcp" in joined or any(code.strip() for code in codes):
        return "mcp_error"
    return None


def classify_task_outcome(
    tool_name: str, response_payload: Any, *, error_code: Any = None
) -> str:
    """Classify business outcome without treating transport success as task success."""
    if _has_timeout_signal(response_payload, error_code):
        return "timeout"
    error_outcome = _error_outcome(response_payload, error_code)
    if error_outcome:
        return error_outcome

    tool = str(tool_name or "").lower()
    data = _payload_data(response_payload)
    if isinstance(data, dict):
        answer_mode = str(data.get("answer_mode") or "").lower()
        route = data.get("route") if isinstance(data.get("route"), dict) else {}
        route_mode = str(route.get("mode") or "").lower()
        if answer_mode == "no_answer" or route_mode == "no_answer" or data.get("no_match"):
            return "no_answer"

    if tool in {"search", "search_fulltext"}:
        if isinstance(data, list):
            return "non_empty" if data else "empty"
        if isinstance(data, dict):
            for key in ("results", "items", "hits", "documents", "sources"):
                if isinstance(data.get(key), list):
                    return "non_empty" if data[key] else "empty"
        return "empty"

    if tool in {"ask", "ask_with_query"}:
        answer = data.get("answer") if isinstance(data, dict) else None
        return "non_empty" if isinstance(answer, str) and answer.strip() else "empty"

    if tool == "graph_traverse" or "graph" in tool:
        if isinstance(data, dict):
            nodes = data.get("nodes")
            edges = data.get("edges")
            if (isinstance(nodes, list) and nodes) or (isinstance(edges, list) and edges):
                return "graph_result"
        return "empty"

    if tool in {"execute_query", "structured_query", "query"}:
        if isinstance(data, list):
            return "structured_result" if data else "empty"
        if isinstance(data, dict):
            for key in ("rows", "items", "data", "results"):
                if isinstance(data.get(key), list):
                    return "structured_result" if data[key] else "empty"
        return "empty"

    if tool in {"read", "read_knowledge", "get_knowledge"}:
        if isinstance(data, str):
            return "non_empty" if data.strip() else "empty"
        if isinstance(data, dict):
            content = data.get("content") or data.get("text") or data.get("body")
            return "non_empty" if isinstance(content, str) and content.strip() else "empty"
        return "empty"

    if isinstance(data, list):
        return "non_empty" if data else "empty"
    if isinstance(data, dict):
        return "non_empty" if data else "empty"
    if isinstance(data, str):
        return "non_empty" if data.strip() else "empty"
    return "unknown"


def _default_required_keys(tool: str) -> list[str]:
    return {
        "search": ["query"],
        "search_fulltext": ["query"],
        "ask": ["question"],
        "ask_with_query": ["question", "search_query"],
    }.get(tool, [])


def validate_argument_contract(
    tool: str,
    raw: dict[str, Any],
    executed: dict[str, Any],
    *,
    required_argument_keys: list[str] | None = None,
    forbidden_argument_keys: list[str] | None = None,
    argument_types: dict[str, str] | None = None,
    recommended_flow: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    required = list(dict.fromkeys(_default_required_keys(tool) + list(required_argument_keys or [])))
    forbidden = list(forbidden_argument_keys or [])
    missing = [key for key in required if key not in executed]
    forbidden_present = [key for key in forbidden if key in executed]
    type_map = {
        "str": str,
        "string": str,
        "int": int,
        "integer": int,
        "float": (int, float),
        "number": (int, float),
        "bool": bool,
        "boolean": bool,
        "list": list,
        "array": list,
        "dict": dict,
        "object": dict,
    }
    invalid_types: list[str] = []
    for key, expected_name in (argument_types or {}).items():
        expected_type = type_map.get(str(expected_name).lower())
        if key in executed and expected_type is not None and not isinstance(executed[key], expected_type):
            invalid_types.append(key)
    graph_start_ok = True
    if tool == "graph_traverse" and not recommended_flow:
        graph_start_ok = bool(executed.get("start_ids")) or bool(
            executed.get("start_type")
            and any(
                key in executed
                for key in ("start_selector", "start_name", "start_query", "start_filter")
            )
        )
        if not graph_start_ok:
            missing.append("start_ids_or_start_type_selector")
    raw_equals_executed = raw == executed
    return {
        "required_keys_present": not missing,
        "forbidden_keys_absent": not forbidden_present,
        "types_valid": not invalid_types,
        "raw_equals_executed": raw_equals_executed,
        "missing_keys": missing,
        "forbidden_keys_present": forbidden_present,
        "invalid_type_keys": invalid_types,
        "unexpected_mutations": [] if raw_equals_executed else ["arguments_changed"],
        "graph_start_valid": graph_start_ok,
    }


def _values_from_previous(payload: Any, spec: Any) -> Any:
    if not isinstance(spec, dict):
        return None
    current = payload
    for part in str(spec.get("path") or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    field = spec.get("field")
    if field and isinstance(current, list):
        values = [item.get(field) for item in current if isinstance(item, dict) and item.get(field)]
        return values if spec.get("as_list", True) else (values[0] if values else None)
    return current


async def execute_recommended_route(
    client: Any,
    *,
    tool: str,
    recommended_arguments: dict[str, Any],
    required_argument_keys: list[str] | None = None,
    forbidden_argument_keys: list[str] | None = None,
    argument_types: dict[str, str] | None = None,
    recommended_flow: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute the Agent contract exactly, including explicit multi-step flows."""
    raw_arguments = deepcopy(recommended_arguments)
    executed_arguments = deepcopy(recommended_arguments)
    contract = validate_argument_contract(
        tool,
        raw_arguments,
        executed_arguments,
        required_argument_keys=required_argument_keys,
        forbidden_argument_keys=forbidden_argument_keys,
        argument_types=argument_types,
        recommended_flow=recommended_flow,
    )
    contract_ok = all(
        contract[key]
        for key in ("required_keys_present", "forbidden_keys_absent", "types_valid", "raw_equals_executed")
    )
    base = {
        "recommended_arguments_raw": raw_arguments,
        "executed_arguments": executed_arguments,
        "arguments_exact_match": raw_arguments == executed_arguments,
        "argument_contract": contract,
        "route_contract_ok": contract_ok,
        "protocol_ok": False,
        "timed_out": False,
        "task_outcome": "validation_error" if not contract_ok else "unknown",
        "task_completed": False,
        "empty_result_detected": False,
        "timeout_signal_detected": False,
        "exec_elapsed_ms": None,
        "raw_exec_response": None,
        "raw_flow_responses": [],
        "executed_flow": [],
        "recommended_flow_executed": False,
    }
    if not contract_ok:
        return base

    try:
        if recommended_flow:
            previous_payload: Any = None
            all_protocol_ok = True
            total_elapsed = 0
            final_tool = tool
            final_record: dict[str, Any] | None = None
            for step in recommended_flow:
                step_tool = str(step.get("tool") or "")
                step_args = deepcopy(step.get("arguments") or {})
                for key, spec in (step.get("arguments_from_previous") or {}).items():
                    step_args[key] = _values_from_previous(previous_payload, spec)
                record = await call_tool(client, step_tool, step_args)
                base["executed_flow"].append({"tool": step_tool, "arguments": deepcopy(step_args)})
                base["raw_flow_responses"].append(record)
                all_protocol_ok = all_protocol_ok and bool(record.get("response_ok"))
                total_elapsed += int(record.get("elapsed_ms") or 0)
                previous_payload = record.get("payload")
                final_tool = step_tool
                final_record = record
                outcome = classify_task_outcome(
                    step_tool, previous_payload, error_code=record.get("error_code")
                )
                if outcome in {
                    "timeout",
                    "validation_error",
                    "provider_error",
                    "mcp_error",
                    "transport_error",
                    "cancelled",
                }:
                    break
            base["recommended_flow_executed"] = len(base["executed_flow"]) == len(recommended_flow)
            base["protocol_ok"] = all_protocol_ok
            base["exec_elapsed_ms"] = total_elapsed
            base["raw_exec_response"] = final_record
            if final_record is None:
                base["task_outcome"] = "validation_error"
            elif not final_record.get("response_ok") and _error_outcome(
                final_record.get("payload"), final_record.get("error_code")
            ) is None:
                base["task_outcome"] = "transport_error"
            else:
                base["task_outcome"] = classify_task_outcome(
                    final_tool,
                    final_record.get("payload"),
                    error_code=final_record.get("error_code"),
                )
        else:
            record = await call_tool(client, tool, executed_arguments)
            base["protocol_ok"] = bool(record.get("response_ok"))
            base["exec_elapsed_ms"] = record.get("elapsed_ms")
            base["raw_exec_response"] = record
            if not record.get("response_ok") and _error_outcome(
                record.get("payload"), record.get("error_code")
            ) is None:
                base["task_outcome"] = "transport_error"
            else:
                base["task_outcome"] = classify_task_outcome(
                    tool, record.get("payload"), error_code=record.get("error_code")
                )
    except (TimeoutError, asyncio.TimeoutError):
        base["task_outcome"] = "timeout"
    except Exception as exc:  # noqa: BLE001
        base["task_outcome"] = "transport_error"
        base["raw_exec_response"] = {
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        }
    base["timed_out"] = base["task_outcome"] == "timeout"
    base["timeout_signal_detected"] = base["timed_out"]
    base["empty_result_detected"] = base["task_outcome"] == "empty"
    base["task_completed"] = (
        base["protocol_ok"] and base["task_outcome"] in TASK_SUCCESS_OUTCOMES
    )
    return base


async def with_stdio_client(home: Path, coro_fn):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    env = os.environ.copy()
    env["SHINEHE_HOME"] = str(home)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    transport = StdioTransport(
        command=sys.executable,
        args=[str(ROOT / "run_mcp.py"), "--config", str(home / "config.yaml")],
        env=env,
        cwd=str(ROOT),
    )
    async with Client(transport) as client:
        return await coro_fn(client)


async def with_http_client(home: Path, coro_fn):
    from fastmcp import Client

    server = HttpMcpServer(home)
    server.start(skip_migration_gate=False)
    try:
        async with Client(server.url) as client:
            return await coro_fn(client)
    finally:
        server.stop()


async def run_retrieval_channel(client: Any, mode: str, limit: int = 60) -> dict:
    """mode: fts | hybrid (default search tool). Vector-only needs config search_mode."""
    gold = load_jsonl(DATA / "production_pilot_retrieval.jsonl")[:limit]
    rows = []
    for g in gold:
        if mode == "fts":
            tool = "search_fulltext"
            args: dict[str, Any] = {"query": g["query"], "limit": 10}
        else:
            # search(query, top_k, filter) — channel controlled by server config
            tool = "search"
            args = {"query": g["query"], "top_k": 10}
        rec = await call_tool(client, tool, args)
        payload = rec.get("payload")
        got = _extract_ids(payload)
        rows.append({
            **{k: g[k] for k in ("id", "query", "expected_ids", "acceptable_ids", "forbidden_ids") if k in g},
            "got_ids": got,
            "response_ok": rec.get("response_ok"),
            "elapsed_ms": rec.get("elapsed_ms"),
            "channel": mode,
            "error": rec.get("error_code"),
            "payload_keys": list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
        })
    metrics = score_retrieval(rows)
    # Precision failure dump for remediation
    failures = []
    for row in rows:
        exp = set(row.get("expected_ids") or []) | set(row.get("acceptable_ids") or [])
        got5 = list(row.get("got_ids") or [])[:5]
        if not exp:
            continue
        hit = len(set(got5) & exp)
        prec = hit / 5.0
        if prec < 0.4 or not (set(got5) & exp):
            failures.append(
                {
                    "id": row.get("id"),
                    "query": row.get("query"),
                    "channel": mode,
                    "precision_at_5": prec,
                    "expected_ids": list(exp)[:10],
                    "got_ids": got5,
                }
            )
    return {
        "channel": mode,
        "n": len(rows),
        "metrics": metrics_to_jsonable(metrics),
        "rows": rows,
        "precision_failures": failures,
    }


async def run_no_answer(client: Any, limit: int = 30) -> dict:
    gold = load_jsonl(DATA / "production_pilot_no_answer.jsonl")[:limit]
    rows = []
    for g in gold:
        srec = await call_tool(client, "search", {"query": g["query"], "top_k": 5})
        arec = await call_tool(client, "ask", {"question": g["query"]})
        sp = srec.get("payload")
        ap = arec.get("payload")
        adata = ap.get("data") if isinstance(ap, dict) and isinstance(ap.get("data"), dict) else ap
        answer = ""
        mode = ""
        sources = []
        if isinstance(adata, dict):
            answer = str(adata.get("answer") or "")
            mode = str(adata.get("answer_mode") or "")
            sources = adata.get("sources") or []
        rows.append({
            "id": g["id"],
            "query": g["query"],
            "expected_no_answer": True,
            "search_no_match": _no_match(sp),
            "ask_answer_mode": mode,
            "answer": answer,
            "sources": sources,
            "search_ok": srec.get("response_ok"),
            "ask_ok": arec.get("response_ok"),
            "elapsed_ms": (srec.get("elapsed_ms") or 0) + (arec.get("elapsed_ms") or 0),
        })
    return {"n": len(rows), "metrics": metrics_to_jsonable(score_no_answer(rows)), "rows": rows}


async def run_numeric(client: Any, limit: int = 25) -> dict:
    gold = load_jsonl(DATA / "production_pilot_numeric_units.jsonl")[:limit]
    rows = []
    for g in gold:
        rec = await call_tool(client, "search", {"query": g["query"], "top_k": 5})
        payload = rec.get("payload")
        rows.append({
            "id": g["id"],
            "query": g["query"],
            "expected_no_answer": g.get("expected_no_answer", False),
            "expected_ids": g.get("expected_ids") or [],
            "expected_units": g.get("expected_units") or [],
            "forbidden_units": g.get("forbidden_units") or [],
            "forbidden_ids": g.get("forbidden_ids") or [],
            "got_ids": _extract_ids(payload),
            "got_top_texts": _texts(payload),
            "search_no_match": _no_match(payload),
            "response_ok": rec.get("response_ok"),
        })
    return {"n": len(rows), "metrics": metrics_to_jsonable(score_numeric_units(rows)), "rows": rows}


async def run_routing(client: Any, limit: int = 40) -> dict:
    gold = load_jsonl(DATA / "production_pilot_routing.jsonl")[:limit]
    rows = []
    for g in gold:
        rrec = await call_tool(client, "route_query", {"question": g["query"]})
        payload = rrec.get("payload")
        data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
        mode = tool = ""
        args: dict[str, Any] = {}
        recommended_flow: list[dict[str, Any]] = []
        route_required_keys: list[str] = []
        forbidden_keys: list[str] = []
        argument_types: dict[str, str] = {}
        if isinstance(data, dict):
            mode = str(data.get("mode") or data.get("route") or "")
            tool = str(data.get("recommended_tool") or data.get("tool") or "")
            args = data.get("recommended_arguments") or data.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            recommended_flow = data.get("recommended_flow") or []
            if not isinstance(recommended_flow, list):
                recommended_flow = []
            route_required_keys = data.get("required_argument_keys") or []
            forbidden_keys = data.get("forbidden_argument_keys") or []
            argument_types = data.get("argument_types") or {}
        required_keys = list(
            dict.fromkeys(list(g.get("required_argument_keys") or []) + list(route_required_keys))
        )
        if bool(rrec.get("response_ok")) and tool:
            execution = await execute_recommended_route(
                client,
                tool=tool,
                recommended_arguments=deepcopy(args),
                required_argument_keys=required_keys,
                forbidden_argument_keys=forbidden_keys,
                argument_types=argument_types,
                recommended_flow=recommended_flow,
            )
        else:
            execution = {
                "recommended_arguments_raw": deepcopy(args),
                "executed_arguments": {},
                "arguments_exact_match": False,
                "argument_contract": {
                    "required_keys_present": False,
                    "types_valid": False,
                    "raw_equals_executed": False,
                    "missing_keys": required_keys,
                    "unexpected_mutations": ["route_not_executable"],
                },
                "route_contract_ok": False,
                "protocol_ok": False,
                "timed_out": _has_timeout_signal(payload, rrec.get("error_code")),
                "task_outcome": classify_task_outcome(
                    "route_query", payload, error_code=rrec.get("error_code")
                ),
                "task_completed": False,
                "empty_result_detected": False,
                "timeout_signal_detected": _has_timeout_signal(
                    payload, rrec.get("error_code")
                ),
                "exec_elapsed_ms": None,
                "raw_exec_response": None,
                "recommended_flow_executed": False,
                "executed_flow": [],
                "raw_flow_responses": [],
            }
        rows.append({
            "id": g["id"],
            "query": g["query"],
            "expected_mode": g.get("expected_mode"),
            "expected_tool": g.get("expected_tool"),
            "required_argument_keys": g.get("required_argument_keys") or [],
            "expected_task_outcome": g.get("expected_task_outcome"),
            "got_mode": mode,
            "got_tool": tool,
            "got_arguments": execution["recommended_arguments_raw"],
            "recommended_arguments_raw": execution["recommended_arguments_raw"],
            "executed_arguments": execution["executed_arguments"],
            "arguments_exact_match": execution["arguments_exact_match"],
            "argument_contract": execution["argument_contract"],
            "protocol_ok": execution["protocol_ok"],
            "route_contract_ok": execution["route_contract_ok"],
            "timed_out": execution["timed_out"],
            "task_outcome": execution["task_outcome"],
            "task_completed": execution["task_completed"],
            "empty_result_detected": execution["empty_result_detected"],
            "timeout_signal_detected": execution["timeout_signal_detected"],
            "route_elapsed_ms": rrec.get("elapsed_ms"),
            "exec_elapsed_ms": execution["exec_elapsed_ms"],
            "raw_route_response": rrec,
            "raw_exec_response": execution["raw_exec_response"],
            "recommended_flow": recommended_flow,
            "executed_flow": execution["executed_flow"],
            "raw_flow_responses": execution["raw_flow_responses"],
            "recommended_flow_executed": execution["recommended_flow_executed"],
        })
    return {"n": len(rows), "metrics": metrics_to_jsonable(score_routing(rows)), "rows": rows}


async def run_answers(client: Any, limit: int = 10) -> dict:
    """Real provider answer + citation sampling (cost-controlled limit)."""
    gold = load_jsonl(DATA / "production_pilot_answer_citations.jsonl")[:limit]
    rows = []
    for g in gold:
        rec = await call_tool(client, "ask", {"question": g["question"]})
        payload = rec.get("payload")
        data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
        answer = ""
        mode = ""
        sources = []
        if isinstance(data, dict):
            answer = str(data.get("answer") or "")
            mode = str(data.get("answer_mode") or "answer")
            sources = data.get("sources") or []
        rows.append({
            "id": g["id"],
            "question": g["question"],
            "answer_mode": mode,
            "answer": answer,
            "sources": sources,
            "expected_answer_facts": g.get("expected_answer_facts") or [],
            "forbidden_claims": g.get("forbidden_claims") or [],
            "response_ok": rec.get("response_ok"),
            "elapsed_ms": rec.get("elapsed_ms"),
            "error": rec.get("error_code"),
            "provider": "real",
        })
    return {"n": len(rows), "metrics": metrics_to_jsonable(score_answer_citations(rows)), "rows": rows}


async def run_health(client: Any) -> dict:
    rec = await call_tool(client, "kb_health_check", {})
    caps = await call_tool(client, "kb_capabilities", {})
    tools = await list_tools(client)
    return {
        "health": rec,
        "capabilities": caps,
        "tool_count": len(tools),
        "tools": tools,
    }


async def run_transport_suite(client: Any, *, answer_limit: int, retrieval_limit: int) -> dict:
    health = await run_health(client)
    fts = await run_retrieval_channel(client, "fts", retrieval_limit)
    vector = await run_retrieval_channel(client, "vector", min(20, retrieval_limit))
    hybrid = await run_retrieval_channel(client, "hybrid", retrieval_limit)
    noa = await run_no_answer(client, min(15, 30))
    numeric = await run_numeric(client, min(15, 25))
    routing = await run_routing(client, min(20, 40))
    answers = await run_answers(client, answer_limit)
    return {
        "health": health,
        "fts": fts,
        "vector": vector,
        "hybrid": hybrid,
        "no_answer": noa,
        "numeric": numeric,
        "routing": routing,
        "answers": answers,
    }


def run_concurrency_search(home: Path, concurrency: int, n: int) -> dict:
    """HTTP concurrent search against formal home."""
    from fastmcp import Client

    queries = [r["query"] for r in load_jsonl(DATA / "production_pilot_retrieval.jsonl")[:n]]
    if not queries:
        return {"error": "no queries"}

    server = HttpMcpServer(home)
    server.start()
    results = []

    async def one(q: str) -> dict:
        async with Client(server.url) as client:
            return await call_tool(client, "search", {"query": q, "top_k": 5})

    def sync_one(q: str) -> dict:
        return asyncio.run(one(q))

    t0 = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [pool.submit(sync_one, queries[i % len(queries)]) for i in range(n)]
            for f in as_completed(futs):
                try:
                    results.append(f.result())
                except Exception as exc:  # noqa: BLE001
                    results.append({"response_ok": False, "error_code": str(exc), "elapsed_ms": None})
    finally:
        server.stop()
    elapsed = time.perf_counter() - t0
    oks = [r for r in results if r.get("response_ok")]
    times = [r["elapsed_ms"] for r in results if isinstance(r.get("elapsed_ms"), int)]
    times_sorted = sorted(times)

    def pct(p: float) -> float | None:
        if not times_sorted:
            return None
        idx = min(len(times_sorted) - 1, max(0, int(round((p / 100) * (len(times_sorted) - 1)))))
        return times_sorted[idx]

    return {
        "concurrency": concurrency,
        "n": n,
        "success": len(oks),
        "success_rate": len(oks) / n if n else 0,
        "wall_sec": elapsed,
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
    }


def formal_db_sha() -> str:
    import hashlib

    h = hashlib.sha256()
    with open(ROOT / "data" / "kb.db", "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "http", "both"], default="both")
    parser.add_argument("--answer-limit", type=int, default=5)
    parser.add_argument("--retrieval-limit", type=int, default=30)
    parser.add_argument("--skip-concurrency", action="store_true")
    parser.add_argument("--skip-answers", action="store_true")
    args = parser.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    sha_before = formal_db_sha()
    home = formal_home()
    summary: dict[str, Any] = {
        "started": utc_now(),
        "formal_db_sha_before": sha_before,
        "home": str(home),
    }

    async def _stdio():
        async def body(client):
            return await run_transport_suite(
                client,
                answer_limit=0 if args.skip_answers else args.answer_limit,
                retrieval_limit=args.retrieval_limit,
            )
        return await with_stdio_client(home, body)

    async def _http():
        async def body(client):
            return await run_transport_suite(
                client,
                answer_limit=0 if args.skip_answers else args.answer_limit,
                retrieval_limit=args.retrieval_limit,
            )
        return await with_http_client(home, body)

    if args.transport in ("stdio", "both"):
        print("Running stdio formal suite...", flush=True)
        stdio_res = asyncio.run(_stdio())
        summary["stdio"] = {
            k: (v.get("metrics") if isinstance(v, dict) and "metrics" in v else v)
            for k, v in stdio_res.items()
            if k != "health"
        }
        summary["stdio_health"] = stdio_res.get("health")
        write_jsonl(ART / "retrieval-stdio.jsonl", stdio_res["hybrid"]["rows"])
        write_jsonl(ART / "no-answer-stdio.jsonl", stdio_res["no_answer"]["rows"])
        write_jsonl(ART / "routing.jsonl", stdio_res["routing"]["rows"])
        write_jsonl(ART / "numeric-units.jsonl", stdio_res["numeric"]["rows"])
        write_jsonl(ART / "answers-citations.jsonl", stdio_res["answers"]["rows"])
        write_json(ART / "retrieval-channels-stdio.json", {
            "fts": stdio_res["fts"]["metrics"],
            "vector": stdio_res["vector"]["metrics"],
            "hybrid": stdio_res["hybrid"]["metrics"],
        })
        write_jsonl(
            ART / "precision-failures.jsonl",
            list(stdio_res["hybrid"].get("precision_failures") or [])
            + list(stdio_res["fts"].get("precision_failures") or []),
        )

    if args.transport in ("http", "both"):
        print("Running HTTP formal suite...", flush=True)
        http_res = asyncio.run(_http())
        summary["http"] = {
            k: (v.get("metrics") if isinstance(v, dict) and "metrics" in v else v)
            for k, v in http_res.items()
            if k != "health"
        }
        summary["http_health"] = http_res.get("health")
        write_jsonl(ART / "retrieval-http.jsonl", http_res["hybrid"]["rows"])
        write_jsonl(ART / "no-answer-http.jsonl", http_res["no_answer"]["rows"])

    if not args.skip_concurrency:
        print("Running concurrency search...", flush=True)
        conc = {}
        for c, n in ((1, 10), (5, 20), (10, 30)):
            print(f"  concurrency={c} n={n}", flush=True)
            conc[str(c)] = run_concurrency_search(home, c, n)
        write_json(ART / "real-provider-concurrency.json", conc)
        summary["concurrency"] = conc

    sha_after = formal_db_sha()
    summary["formal_db_sha_after"] = sha_after
    summary["formal_db_unchanged"] = sha_before == sha_after
    summary["finished"] = utc_now()
    write_json(ART / "formal-mcp-suite-summary.json", summary)
    print(json.dumps({
        "formal_db_unchanged": summary["formal_db_unchanged"],
        "stdio_hybrid_recall5": (summary.get("stdio") or {}).get("hybrid"),
        "http_hybrid_recall5": (summary.get("http") or {}).get("hybrid"),
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
