# MCP Ingest Jobs

Use jobs for large files and any import that may exceed MCP request timeouts.

## Decision Flow

1. `kb_capabilities`
2. `ingest_file(dry_run=true)` for a preview
3. `create_ingest_job` for large files, or `ingest_file` for small files
4. `get_job` until the job is completed, failed, or cancelled
5. `structured_query` to verify imported blocks
6. `ask` to answer with sources

## Job Tools

- `create_ingest_job`: creates a file or URL ingest job
- `get_job`: returns status, progress, result, or error
- `list_jobs`: lists recent jobs by status or type
- `cancel_job`: requests cancellation

## Result Shape

Import results include created items, skipped items, failed items, sheet count,
page count, block count, and operation id when available.

Failed items should not hide successful sheets or pages. Agents should report
partial success with the failed item list.
