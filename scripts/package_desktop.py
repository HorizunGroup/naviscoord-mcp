"""Package the standalone runtime and the local desktop plugin, with checksums."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from naviscoord import __version__


def archive(path, files):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for source, name in sorted(files, key=lambda pair: pair[1]):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, source.read_bytes())


def main():
    dist = ROOT / "dist"
    runtime = dist / "portable/naviscoord-mcp"
    index = json.loads((runtime / "runtime-files.json").read_text())
    assert index["version"] == __version__
    for name, digest in index["files"].items():
        source = (runtime / name).resolve()
        assert source.is_relative_to(runtime.resolve())
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, name
    runtime_files = [(p, p.relative_to(runtime).as_posix()) for p in runtime.rglob("*") if p.is_file()]
    runtime_zip = dist / f"naviscoord-runtime-{__version__}-win-x64.zip"
    archive(runtime_zip, [(p, "naviscoord-mcp/" + name) for p, name in runtime_files])
    includes = [".codex-plugin/plugin.json", ".claude-plugin/plugin.json", "assets/icon.png",
                "LICENSE", "NOTICE", "README.md", "README.es.md", "Install-NavisCoord.ps1",
                "docs/INSTALL.md", "docs/PRIVACY.md", "docs/TOOLS.md",
                "scripts/Start-Mcp.ps1", "scripts/Install-Runtime.ps1",
                "scripts/Install-DesktopPlugin.ps1", "scripts/Configure-Clients.ps1"]
    includes += [p.relative_to(ROOT).as_posix() for p in (ROOT / "skills").rglob("*.md")]
    plugin_zip = dist / f"naviscoord-desktop-{__version__}-win-x64.zip"
    archive(plugin_zip, [(ROOT / name, name) for name in includes] + [(p, "runtime/" + name) for p, name in runtime_files])
    artifacts = [runtime_zip, plugin_zip, dist / f"naviscoord-{__version__}-win-x64.mcpb"]
    artifacts += sorted((dist / "addin").glob(f"NavisCoord-{__version__}-addin-NW*.zip"))
    assert len(artifacts) == 6, "All three supported Navisworks builds are required"
    sums = [hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name for p in artifacts]
    (dist / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="ascii")
    print("\n".join(sums))


if __name__ == "__main__":
    main()
