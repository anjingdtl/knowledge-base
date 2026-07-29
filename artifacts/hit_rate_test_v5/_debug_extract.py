import sqlite3
from src.answering.claim_protocol import structured_answer_from_evidence

conn = sqlite3.connect("data/kb.db")
pids = [
    "83fb103cba98000e98863f715dc075c6",
    "73eb9de24badb90bc55db035406b0fdb",
    "f9159242a68d8d9a1268abce68d7a9a3",
]
rows = []
for pid in pids:
    t = conn.execute("select text from retrieval_passages where id=?", (pid,)).fetchone()[0]
    rows.append(
        {
            "passage_id": pid,
            "knowledge_id": "51b17abe",
            "text": t,
            "retrieval_unit": "passage",
            "candidate_type": "passage",
            "block_ids": ["b"],
        }
    )
r = structured_answer_from_evidence(
    question="涉骚扰电话 代理商一个自然月内每个号码处罚金额",
    evidence_rows=rows,
)
print("018", r["answer_mode"], repr(r.get("answer")), r.get("reason"))
print("kept", (r.get("numeric_fact_audit") or {}).get("kept"))

row = conn.execute(
    "select text from retrieval_passages where knowledge_id=? and text like '%1个工作日%' limit 1",
    ("b40b8949-e458-408a-aa75-292b0540516b",),
).fetchone()
if row:
    r2 = structured_answer_from_evidence(
        question="产品问需工单 审核初审和产品评估的工作日时限",
        evidence_rows=[
            {
                "passage_id": "p21",
                "knowledge_id": "b40",
                "text": row[0],
                "retrieval_unit": "passage",
                "candidate_type": "passage",
                "block_ids": ["b"],
            }
        ],
    )
    print("021", r2["answer_mode"], repr(r2.get("answer")), r2.get("reason"))
