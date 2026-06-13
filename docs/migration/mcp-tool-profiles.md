# MCP Tool Profiles — Migration Guide

> From v1.3.0, ShineHeKnowledge uses configurable tool profiles to control which MCP tools are registered.
> This guide helps existing users migrate from v1.2 (51 tools + 51 aliases) to the new profile system.

## What Changed

| Before (v1.2) | After (v1.3) |
|---------------|-------------|
| 51 tools + 51 aliases always registered | 10 core tools by default |
| No profile concept | 5 profiles: core, extended, admin, full, legacy |
| Aliases always on | Aliases only in `legacy` profile or explicit opt-in |
| `write_policy` optional | `write_policy=disabled` by default for new configs |

## Quick Migration

### New Users

No migration needed. `shinehe init` generates a config with `mcp.tool_profile: core`.

### Existing Users (v1.2 config)

If your `config.yaml` has `mcp:` settings but no `tool_profile`, the system auto-detects this as a legacy config and uses the `legacy` profile. **Nothing breaks.**

To explicitly migrate:

```yaml
# Option A: Keep all tools and aliases (identical to v1.2 behavior)
mcp:
  tool_profile: legacy
  enable_legacy_aliases: true

# Option B: Use all tools but without aliases
mcp:
  tool_profile: full
  enable_legacy_aliases: false

# Option C: Adopt the new default (recommended for AI agents)
mcp:
  tool_profile: core
  enable_legacy_aliases: false
  write_policy: disabled
```

## Profile Reference

### `core` (default for new configs)

10 tools optimized for AI agent retrieval:

`ping`, `kb_capabilities`, `search`, `ask`, `read`, `list_knowledge`, `index_path`, `get_job`, `list_jobs`, `reindex_all`

### `extended`

core + 10 advanced tools:

`search_fulltext`, `tags`, `route_query`, `execute_query`, `structured_query`, `explain_query`, `ask_with_query`, `get_source_graph`, `create_ingest_job`, `cancel_job`

### `admin`

extended + 10 CRUD/audit tools:

`create`, `update`, `delete`, `restore_knowledge`, `ingest_url`, `preview_operation`, `get_operation_log`, `undo_operation`, `list_recent_operations`, `query_operation_logs`

### `full`

All non-experimental tools (no Wiki/Graph/Memory unless `experimental_tools_enabled=true`).

### `legacy`

All tools + namespaced aliases (e.g., `kb.search`, `kb.ask`). Identical to v1.2 behavior.

## Experimental Tools

Wiki, Graph, and Agent Memory tools are gated behind `mcp.experimental_tools_enabled=true`.

```yaml
mcp:
  tool_profile: full
  experimental_tools_enabled: true   # enables wiki_*, graph_*, remember_*, etc.
```

## Backward Compatibility

| Scenario | Behavior |
|----------|----------|
| New config (no `mcp:` section) | `core` profile |
| Old config (has `mcp:` but no `tool_profile`) | `legacy` profile (auto-detected) |
| `enable_legacy_aliases: false` | No `kb.*` aliases registered |
| `experimental_tools_enabled: false` | Wiki/Graph/Memory tools hidden |
| `write_policy: disabled` | `index_path` and other write tools reject actual writes; `dry_run=true` still works |

## Tool Count by Profile

| Profile | Tool Count | Alias Count |
|---------|-----------|-------------|
| core | 10 | 0 |
| extended | 20 | 0 |
| admin | 30 | 0 |
| full | ~40 (non-experimental) | 0 |
| legacy | 51 | 51 |

## Verifying Your Profile

Call `kb_capabilities` after connecting. It returns:

```json
{
  "tool_profile": "core",
  "write_policy": "disabled",
  "experimental_tools_enabled": false,
  "visible_tools": ["ask", "get_job", "index_path", ...],
  "hidden_groups": ["wiki", "graph", "memory"],
  "legacy_aliases_enabled": false
}
```
