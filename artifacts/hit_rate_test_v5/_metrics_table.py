import json
from pathlib import Path

rows = []
for label, p in [
    ("R1", "artifacts/hit_rate_test_after_fix"),
    ("R2", "artifacts/hit_rate_test_v2"),
    ("R3", "artifacts/hit_rate_test_v3"),
    ("R4", "artifacts/hit_rate_test_v4"),
    ("R5", "artifacts/hit_rate_test_v5"),
]:
    fp = Path(p) / "final_scored.json"
    if not fp.exists():
        rows.append((label, None))
        continue
    m = json.loads(fp.read_text(encoding="utf-8"))["metrics"]
    rows.append((label, m))

keys = [
    ("Top-1 Accuracy", 0.75, True),
    ("Recall@5", 0.88, True),
    ("Ask Fact Correctness", 0.90, True),
    ("Ask Citation Validity", 0.95, True),
    ("E2E Pass Rate", 0.90, True),
    ("Hallucination Rate", 0.05, False),
    ("False Positive Rate", 0.05, False),
]
lines = [
    "Metric | R1 | R2 | R3 | R4 | R5 | Gate | R5",
    "---|---:|---:|---:|---:|---:|---:|---",
]
for k, thr, higher in keys:
    vals = []
    last = None
    for lab, m in rows:
        if not m or m.get(k) is None:
            vals.append("n/a")
        else:
            last = float(m[k])
            vals.append(f"{last * 100:.2f}%")
    ok = ""
    if last is not None:
        ok = "通过" if ((last >= thr) if higher else (last <= thr)) else "未通过"
    lines.append(
        f"{k} | " + " | ".join(vals) + f" | {thr*100:.0f}% | {ok}"
    )

# P1 counts
p1_line = "P1 count"
for lab, p in [
    ("R1", "artifacts/hit_rate_test_after_fix"),
    ("R2", "artifacts/hit_rate_test_v2"),
    ("R3", "artifacts/hit_rate_test_v3"),
    ("R4", "artifacts/hit_rate_test_v4"),
    ("R5", "artifacts/hit_rate_test_v5"),
]:
    fp = Path(p) / "final_scored.json"
    if not fp.exists():
        p1_line += " | n/a"
        continue
    d = json.loads(fp.read_text(encoding="utf-8"))
    p1_line += f" | {len(d.get('defects', {}).get('P1') or [])}"
p1_line += " | 0 | "
d5 = json.loads(Path("artifacts/hit_rate_test_v5/final_scored.json").read_text(encoding="utf-8"))
p1n = len(d5.get("defects", {}).get("P1") or [])
p1_line += "通过" if p1n == 0 else "未通过"
lines.append(p1_line)

text = "\n".join(lines)
Path("artifacts/hit_rate_test_v5/metrics_comparison.txt").write_text(text, encoding="utf-8")
print(text)
