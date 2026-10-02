"""Generate the public tool reference from the actual registered MCP schema."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from naviscoord.mcp_server import mcp


def _schema(tool):
    """mcp 1.x names it `inputSchema`; mcp 2.x, `input_schema`."""
    schema = getattr(tool, "input_schema", None)
    return schema if schema is not None else tool.inputSchema


async def main():
    tools = sorted(await mcp.list_tools(), key=lambda t: t.name)
    lines = ["# NavisCoord tool reference", "",
             f"{len(tools)} tools generated from the registered server. Names and input schemas are authoritative.", "",
             "Document mutations check the intended document fingerprint. Where a tool exposes `dry_run`, inspect the preview before execution.", ""]
    for tool in tools:
        lines += [f"## `{tool.name}`", "", (tool.description or "").strip(), "",
                  "```json", json.dumps(_schema(tool), indent=2, ensure_ascii=False), "```", ""]
    (ROOT / "docs/TOOLS.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
