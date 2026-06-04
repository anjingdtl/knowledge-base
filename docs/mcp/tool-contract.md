# MCP Tool Contract

Every MCP tool returns a JSON-compatible envelope.

## Success

```json
{
  "ok": true,
  "data": {},
  "meta": {}
}
```

`data` contains the useful payload. `meta` contains pagination, truncation,
counts, route mode, or other execution metadata.

## Failure

```json
{
  "ok": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "resource not found",
    "details": {}
  }
}
```

Agents should branch on `error.code`, not on natural-language text.

Common codes include `NOT_FOUND`, `VALIDATION_ERROR`, `PERMISSION_DENIED`,
`INGEST_FAILED`, `QUERY_PARSE_ERROR`, `JOB_NOT_FOUND`, and `INTERNAL_ERROR`.

## Writes

Write tools return `operation_id` when an audit log entry is created.

Preview calls return `dry_run: true` and `data.would_change`. A `dry_run`
response must not write to the database, vectors, files, or operation log.

## Bounded Payloads

Large result tools use `limit`, `offset`, `truncated`, `next_offset`, and
`total_estimate` in `meta`. Agents should request the next page instead of
asking a tool to return the entire knowledge base.
