# MCP Agent Usage

This knowledge base is MCP-first. Agents should use tool outputs as JSON
envelopes, not as prose status messages.

## Research Flow

Use this order for exploratory work:

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

Use this order for edits:

1. `read`
2. `preview_operation`
3. `update(dry_run=true)`
4. `update`
5. `get_operation_log`

Never perform a write before a preview or `dry_run`. Keep the returned
`operation_id` so the user or another agent can audit or undo the change.

## Import Flow

Use this order for files and URLs:

1. `kb_capabilities`
2. `create_ingest_job` or `ingest_file`
3. `get_job`
4. `structured_query`
5. `ask`

Large files should go through jobs. Poll `get_job` until completion, then verify
the imported blocks with `structured_query` and answer with `ask`.

## Sourced Q&A Flow

Use this order for answerable questions:

1. `route_query`
2. `ask(include_graph=true, include_context=true)`
3. `get_source_graph`
4. `read`

Always inspect sources when the answer will be used for decisions.
