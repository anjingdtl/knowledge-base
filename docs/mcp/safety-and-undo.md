# MCP Safety And Undo

Writes must be previewed and audited.

## Update Checklist

1. `read(item_id, include_blocks=true)`
2. `preview_operation(operation="update", item_id=..., fields...)`
3. `update(..., dry_run=true)`
4. `update(...)`
5. `get_operation_log(operation_id)`

If the user asks to revert, call `undo_operation(operation_id)`.

## Delete Checklist

`delete` is soft delete by default. Preview first with `preview_operation` or
`delete(dry_run=true)`. After deletion, retain the `operation_id`; restore with
`restore_knowledge` or `undo_operation`.

## Audit Fields

Operation logs include source, operator, target type, target id,
`snapshot_before`, `snapshot_after`, metadata, and created time.

## Agent Rule

Never treat a write as complete until `get_operation_log` confirms the returned
`operation_id`.
