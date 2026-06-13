# MCP Agent Usage

This knowledge base is MCP-first. Agents should use tool outputs as JSON
envelopes, not as prose status messages.

## Default Tool Face (core profile)

The default `core` profile exposes 10 tools optimized for retrieval:

`ping`, `kb_capabilities`, `search`, `ask`, `read`, `list_knowledge`,
`index_path`, `get_job`, `list_jobs`, `reindex_all`

Start with `kb_capabilities` to learn the active profile, visible tools,
write policy, and recommended flows.

## Research Flow

Use this order for exploratory work:

1. `kb_capabilities`
2. `search` (or `ask` for direct answers)
3. `read` (to inspect exact source blocks)

For advanced retrieval (extended profile and above):

1. `kb_capabilities`
2. `route_query`
3. `execute_query` or `ask`
4. `get_source_graph`
5. `read`

Start with `kb_capabilities` to learn available tools, payload limits, and
recommended flows. Use `route_query` before expensive retrieval so the agent can
choose structured, graph, or hybrid search. Use `read` at the end to inspect the
exact source blocks behind an answer.

## Safe Update Flow

Use this order for edits (requires `admin` profile or above):

1. `read`
2. `preview_operation`
3. `update(dry_run=true)`
4. `update`
5. `get_operation_log`

Never perform a write before a preview or `dry_run`. Keep the returned
`operation_id` so the user or another agent can audit or undo the change.

## Import Flow

Use this order for files and directories:

1. `kb_capabilities`
2. `index_path` (core) or `create_ingest_job` (extended)
3. `get_job`
4. `search` or `ask`

Large directories should go through `index_path` which automatically creates
async jobs when thresholds are exceeded. Poll `get_job` until completion, then
verify with `search` and answer with `ask`.

## Sourced Q&A Flow

Use this order for answerable questions:

1. `ask` (core profile returns cited answers directly)
2. `read` (to inspect source blocks)

For advanced flows (extended profile):

1. `route_query`
2. `ask(include_graph=true, include_context=true)`
3. `get_source_graph`
4. `read`

Always inspect sources when the answer will be used for decisions.

## Citation Structure

Both `search` and `ask` return structured citations:

```json
{
  "document": "filename.md",
  "path": "D:/docs/filename.md",
  "knowledge_id": "doc_001",
  "block_id": "doc_001_block_07",
  "location": {
    "page": null,
    "sheet": null,
    "slide": null,
    "heading_path": ["Section", "Subsection"],
    "paragraph_index": 12,
    "line_start": null,
    "line_end": null
  },
  "score": 0.87,
  "score_breakdown": {
    "vector": 0.82,
    "keyword": 0.64,
    "rrf": 0.031,
    "rerank": 0.87
  },
  "match_channels": ["semantic", "keyword"],
  "reason": "semantic + keyword match; reranked",
  "text": "Original text from the block."
}
```
