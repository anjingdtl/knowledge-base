# 真实 Embedding 评测报告

> 日期：2026-07-13  
> 版本：v1.7.0  
> 模型：`BAAI/bge-m3` @ SiliconFlow（配置 `embedding.*`）  
> Tag：`v1.7.0`

## 1. 范围

| 评测 | 命令 | 说明 |
|---|---|---|
| Retrieval（真实向量） | `python evals/run_retrieval_eval.py --all --engine real-embedding` | fixtures 全量 cosine |
| Claim Matching（真实向量） | `python evals/run_wiki_v2_semantic_eval.py` | wiki_v2 黄金集 12 例 |
| Hybrid 离线黄金集 | `python evals/run_hybrid_eval.py --strict` | 不调用 embedding（对照） |

产物：

- `artifacts/eval/retrieval-real-embedding.json`
- `artifacts/eval/wiki-v2-semantic-real-embedding.json`

## 2. Retrieval 结果（real-embedding）

合并 retrieval_code / retrieval_table / retrieval_zh（排除 no_answer 集）：

| 指标 | 值 |
|---|---:|
| Recall@5 | **1.0000** |
| MRR | **1.0000** |
| nDCG@10 | **0.8579** |
| Citation Location Completeness | **1.0000** |
| No-Answer Accuracy | **1.0000**（cosine top-score &lt; 0.55 阈值） |
| Latency P50 / P95 | **502.9ms / 533.6ms**（含远程 API） |

分数据集：code / table / zh 均为 R@5=1.0、MRR=1.0。

说明：

- 非 fake-embedding；真实 API 往返。
- 纯向量 top-k 总会返回结果，no_answer 使用 cosine 阈值 0.55（域内命中约 0.59–0.67，域外约 0.43–0.51）。
- 个别 query top-5 可能出现 distractor（must_not），但不影响主指标 R@5/MRR 满分。

## 3. Wiki Claim Matching（真实 embedding）

12 cases / 24 embed calls：

| action | precision | tp / fp / fn |
|---|---:|---|
| new | 1.00 | 1/0/0 |
| supports | 0.50 | 1/1/0 |
| contradicts | 1.00 | 1/0/0 |
| duplicate | 1.00 | 1/0/0 |
| unresolved | 0.71 | 5/2/1 |
| refines | n/a | 0/0/1 |
| supersedes | n/a | 0/0/1 |

解读：主路径（new / contradicts / duplicate）稳定；C2 保守 demote 使部分 refines/supersedes 落为 unresolved（与 matcher 保守策略一致，非回归刷数）。

## 4. Hybrid 离线对照

```text
Hybrid Eval PASS: cases=175
raw=1.0 wiki=1.0 hybrid=1.0
stale=0 unsupported=0 cite=1.0 conflict_recall=1.0
```

## 5. 结论

- **真实 embedding 检索在 fixture 黄金集上 Recall/MRR 满分**，满足发布后质量抽检。
- Claim 语义匹配整体可用，未出现系统性崩溃；细粒度 action 仍有保守 demote 空间。
- **不**将 fake-embedding 结果与本报告混用宣传。

## 6. 复现

```bash
# 需配置 embedding API（config / SHINEHE_EMBEDDING_API_KEY）
python evals/run_retrieval_eval.py --all --engine real-embedding --report json --output artifacts/eval/retrieval-real-embedding.json
python evals/run_wiki_v2_semantic_eval.py --json
python evals/run_hybrid_eval.py --strict
```
