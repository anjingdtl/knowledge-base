"""记录 Canonical Wiki v2 迁移前的质量基线。

聚合 pytest 计数 / ruff / mypy / retrieval eval / wiki eval 到
``artifacts/eval/canonical-v2-baseline.json``,供后续 Phase 回归对比。

可复现:retrieval eval 用 CI 同款 fake-embedding(确定性,零 LLM)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "eval" / "canonical-v2-baseline.json"


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _pytest_count() -> dict:
    rc, out = _run([sys.executable, "-m", "pytest", "tests/", "--co", "-q"])
    # 最后一行形如 "1243 tests collected (5.38s)"
    lines = [ln for ln in out.splitlines() if "tests collected" in ln.lower()]
    return {"collected_tests_rc": rc, "summary": lines[-1].strip() if lines else out.strip().splitlines()[-1] if out.strip() else ""}


def _ruff() -> dict:
    rc, out = _run(["ruff", "check", "src", "tests", "evals", "tools", "scripts"])
    return {"rc": rc, "tail": "\n".join(out.splitlines()[-3:])}


def _mypy() -> dict:
    rc, out = _run(["mypy", "src", "tools"])
    return {"rc": rc, "tail": "\n".join(out.splitlines()[-3:])}


def _retrieval_eval() -> dict:
    rc, out = _run([
        sys.executable, "evals/run_retrieval_eval.py", "--all", "--fake-embedding",
        "--baseline", "evals/baselines/local.json", "--max-regression", "0.05",
        "--report", "json",
    ])
    return {"rc": rc, "tail": "\n".join(out.splitlines()[-8:])}


def _wiki_eval() -> dict:
    # wiki/ 不存在或为空时记 N/A,不阻断基线
    out_path = OUT.parent / "wiki-eval-tmp.json"
    rc, out = _run([
        sys.executable, "evals/run_wiki_eval.py", "--source", "fs",
        "--output", str(out_path),
    ])
    result: dict = {"rc": rc, "tail": "\n".join(out.splitlines()[-8:])}
    # 如果成功生成了 JSON，读取并内联
    if out_path.exists():
        try:
            result["report"] = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        out_path.unlink(missing_ok=True)
    return result


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "version": "1.5.2",
        "pytest": _pytest_count(),
        "ruff": _ruff(),
        "mypy": _mypy(),
        "retrieval_eval": _retrieval_eval(),
        "wiki_eval": _wiki_eval(),
    }
    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"baseline written -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
