"""Publish a validated local MCPB with its separate, complete Smithery server card.

Requires SMITHERY_API_KEY in the environment and the httpx package. This uses
Smithery's documented release API because CLI 4.11.1 forwards MCPB's abbreviated
manifest.tools as a server card, which the API rejects for missing inputSchema.
"""
import argparse
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import quote

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("name", help="Existing account namespace/server name")
    args = parser.parse_args()
    if args.bundle.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Bundle exceeds Smithery's 25 MB limit")
    with zipfile.ZipFile(args.bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        card = json.loads(archive.read("smithery-server-card.json"))
    if {t["name"] for t in card["tools"]} != {t["name"] for t in manifest["tools"]}:
        raise ValueError("Tool inventories differ")
    if any(not isinstance(t.get("inputSchema"), dict) for t in card["tools"]):
        raise ValueError("Missing tool input schema")
    if card["serverInfo"]["version"] != manifest["version"]:
        raise ValueError("Server-card version differs from bundle")
    payload = {"type": "stdio", "runtime": "binary", "serverCard": card,
               "configSchema": {"type": "object", "properties": {"output_root": {
                   "type": "string", "default": "", "title": "Output directory",
                   "description": "Optional local export folder; empty uses NavisCoord's default."}}}}
    token = os.environ["SMITHERY_API_KEY"]
    base = f"https://api.smithery.ai/servers/{quote(args.name, safe='')}"
    headers = {"Authorization": f"Bearer {token}"}
    created = httpx.put(base, headers=headers, json={
        "displayName": "NavisCoord by HorizunGroup",
        "description": "53 local MCP tools for Autodesk Navisworks Manage 2024-2026 on Windows: "
                       "clash analysis, verified groups, revision comparisons and illustrated reports. "
                       "First launch downloads the complete SHA256-verified runtime; later launches use the local cache.",
    }, timeout=60)
    if created.status_code not in (200, 201):
        raise RuntimeError(f"Smithery server registration failed ({created.status_code}): {created.text}")
    with args.bundle.open("rb") as bundle:
        response = httpx.put(
            f"{base}/releases",
            headers=headers,
            data={"payload": json.dumps(payload)},
            files={"bundle": (args.bundle.name, bundle, "application/octet-stream")}, timeout=120,
        )
    if response.status_code not in (200, 201, 202):
        raise RuntimeError(f"Smithery rejected publication ({response.status_code}): {response.text}")
    result = response.json()
    print(json.dumps({k: result[k] for k in ("deploymentId", "status", "mcpUrl", "warnings") if k in result}))


if __name__ == "__main__":
    main()
