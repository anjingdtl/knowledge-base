from pathlib import Path

text = Path("src/mcp/server.py").read_text(encoding="utf-8")
start = text.index("_TOOL_METADATA = {")
end = text.index("# ---- Phase 4.2: Agent Memory Tools ----")
block = text[start:end].rstrip() + "\n"
block = block.replace("_TOOL_METADATA", "TOOL_METADATA", 1)
block = block.replace("_TOOL_ALIASES", "TOOL_ALIASES", 1)
# keep underscore aliases for compatibility
block += "\n_TOOL_METADATA = TOOL_METADATA\n_TOOL_ALIASES = TOOL_ALIASES\n"
header = '"""MCP tool catalog — metadata and namespaced aliases."""\nfrom __future__ import annotations\n\n'
Path("src/mcp/tool_catalog.py").write_text(header + block, encoding="utf-8")
print("wrote tool_catalog.py", Path("src/mcp/tool_catalog.py").stat().st_size)
