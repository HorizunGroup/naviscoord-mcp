"""Generate the public tool reference from the actual registered MCP schema."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from naviscoord.mcp_server import mcp


async def main():
    tools = sorted(await mcp.list_tools(), key=lambda t: t.name)
    lines = ["# NavisCoord tool reference", "",
             f"{len(tools)} tools generated from the registered server. Names and input schemas are authoritative.", "",
             "Document mutations check the intended document fingerprint. Where a tool exposes `dry_run`, inspect the preview before execution.", ""]
    for tool in tools:
        lines += [f"## `{tool.name}`", "", (tool.description or "").strip(), "",
                  "```json", json.dumps(tool.inputSchema, indent=2, ensure_ascii=False), "```", ""]
    (ROOT / "docs/TOOLS.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
