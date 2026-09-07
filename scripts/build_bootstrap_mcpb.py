"""Stage a small MCPB using the approved tag's checksum-verifying launcher.

The first launch downloads the complete stable runtime. Subsequent launches
verify the cached files and work without another download. No tools are removed.
Pack the resulting dist/mcpb-bootstrap directory with the official MCPB CLI.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def describe_tools(executable: Path) -> list:
    async with stdio_client(StdioServerParameters(command=str(executable))) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return (await session.list_tools()).tools


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="Verified full release MCPB")
    args = parser.parse_args()
    stage = ROOT / "dist/mcpb-bootstrap"
    if stage.resolve().parent != (ROOT / "dist").resolve() or stage.is_symlink():
        raise RuntimeError("Unsafe bootstrap staging directory")
    with zipfile.ZipFile(args.bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        runtime = ROOT / "dist/portable/naviscoord-mcp"
        for info in archive.infolist():
            if info.filename.startswith("runtime/") and not info.is_dir():
                relative = info.filename.removeprefix("runtime/")
                candidate = (runtime / relative).resolve()
                if not candidate.is_relative_to(runtime.resolve()):
                    raise ValueError("Unsafe runtime path in bundle")
                if hashlib.sha256(candidate.read_bytes()).digest() != hashlib.sha256(archive.read(info)).digest():
                    raise ValueError(f"Runtime does not match the release bundle: {relative}")
        registered = asyncio.run(asyncio.wait_for(describe_tools(runtime / "naviscoord-mcp.exe"), timeout=60))
        schemas = {tool.name: tool.inputSchema for tool in registered}
        if set(schemas) != {tool["name"] for tool in manifest["tools"]}:
            raise ValueError("Runtime tool inventory differs from the release manifest")
        # MCPB forbids inputSchema in manifest.tools, while Smithery's server
        # card requires it. Keep the two valid schemas in separate documents.
        card = {"serverInfo": {"name": manifest["name"], "version": manifest["version"],
                               "title": manifest["display_name"]},
                "tools": [tool.model_dump(exclude_none=True) for tool in registered],
                "resources": [], "prompts": []}
        release = manifest["version"]
        if not all(part.isdigit() for part in release.split(".")) or len(release.split(".")) != 3:
            raise ValueError("A stable release is required")
        tag = f"v{release}"
        source = subprocess.check_output(["git", "rev-parse", f"{tag}^{{commit}}"], cwd=ROOT, text=True).strip()
        files = {}
        for relative in ("scripts/Start-Mcp.ps1", "scripts/Install-Runtime.ps1", ".codex-plugin/plugin.json"):
            files[relative] = subprocess.check_output(["git", "show", f"{source}:{relative}"], cwd=ROOT)
        if json.loads(files[".codex-plugin/plugin.json"])["version"] != release:
            raise ValueError("Launcher and bundle versions differ")
        for relative in ("README.md", "LICENSE", "NOTICE", "docs/PRIVACY.md", "assets/icon.png"):
            files[relative] = archive.read(relative)
    manifest["server"]["entry_point"] = "scripts/Start-Mcp.ps1"
    manifest["server"]["mcp_config"]["command"] = "powershell.exe"
    manifest["server"]["mcp_config"]["args"] = [
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "${__dirname}/scripts/Start-Mcp.ps1",
    ]
    manifest["long_description"] += (
        "\n\nThis installer bundle downloads the complete versioned Windows runtime "
        "from the public GitHub release on first launch and verifies its SHA256. "
        "Internet access is required for that first download or repair; subsequent "
        "launches use the verified local cache. All coordination tools are retained."
    )
    if stage.exists():
        shutil.rmtree(stage)
    for relative, data in files.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (stage / "bootstrap-source.json").write_text(json.dumps({"tag": tag, "commit": source}, indent=2) + "\n")
    (stage / "smithery-server-card.json").write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Staged {len(manifest['tools'])} tools; launcher source {source}; {stage}")


if __name__ == "__main__":
    main()
