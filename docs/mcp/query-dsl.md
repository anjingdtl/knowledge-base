# MCP Query DSL

Use QuerySpec when a question can be answered by structured filters or graph
traversal.

## Routing

Call `route_query(question)` first. It returns a mode:

- `structured`: use `execute_query` or `structured_query`
- `graph`: use `graph_traverse` or `execute_query(type="graph")`
- `hybrid`: use `ask` or semantic `search`

## Structured Query

`structured_query` and `execute_query(type="structured")` accept QuerySpec-like
filters and return envelope data. Use `include_blocks=true` when the answer must
cite block-level evidence.

Example:

```json
{
  "filter": {"tag": "project"},
  "include_blocks": true,
  "limit": 20,
  "offset": 0
}
```

## Graph Query

Use `graph_traverse` or `execute_query(type="graph")` for relationship,
backlink, parent-chain, and local-neighborhood questions. Always provide
bounded values such as `max_depth`, `limit`, and `offset`.

## Explainability

Use `explain_query` when an agent needs to show matched filters, expanded tags,
link targets, SQL summary, graph traversal, or fallback reason before answering.
