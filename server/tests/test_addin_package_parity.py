"""The add-in ZIP the release builds is the ZIP the installer accepts.

`scripts/Build-Release.ps1` decides what goes into each
`NavisCoord-X.Y.Z-addin-NWYYYY.zip`; `Install-NavisCoord.ps1` refuses any ZIP
whose entries are not exactly its `$ManagedFiles`. The two lists lived in two
scripts with nothing comparing them, and they disagreed: the release packed
four files, the installer demanded eight (the ribbon layout twice and its two
icons). The 1.0.0 and 1.1.0 ZIPs were published that way, and the official
installer could install neither of them — found only when 1.1.0 was installed
from its own release.

Read statically from both scripts, so the comparison runs on every CI leg
without PowerShell or Navisworks.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _ps_array(text: str, variable: str) -> list[str]:
    match = re.search(rf"\${variable}\s*=\s*@\((.*?)\)", text, re.DOTALL)
    assert match, f"no encuentro ${variable}"
    return re.findall(r"'([^']+)'", match.group(1))


def _ps_scalar(text: str, variable: str) -> str:
    match = re.search(rf"\${variable}\s*=\s*'([^']+)'", text)
    assert match, f"no encuentro ${variable}"
    return match.group(1)


def built_entries() -> set[str]:
    text = (ROOT / "scripts" / "Build-Release.ps1").read_text(encoding="utf-8")
    dll = re.search(r"Dll\s*=\s*'([^']+)'", text).group(1)
    return {dll, _ps_scalar(text, "profileName"), *_ps_array(text, "legal"), *_ps_array(text, "ribbon")}


def accepted_entries() -> set[str]:
    text = (ROOT / "Install-NavisCoord.ps1").read_text(encoding="utf-8")
    return set(_ps_array(text, "ManagedFiles"))


def test_release_zip_matches_what_the_installer_accepts() -> None:
    built, accepted = built_entries(), accepted_entries()
    assert built == accepted, (
        f"Build-Release empaqueta {sorted(built)} pero Install-NavisCoord exige {sorted(accepted)}"
    )


def test_ribbon_layout_and_icons_travel_in_the_package() -> None:
    built = built_entries()
    for name in ("NavisCoordRibbon.xaml", "en-US/NavisCoordRibbon.xaml", "nc_16.png", "nc_32.png"):
        assert name in built, f"sin {name} la pestaña NavisCoord no aparece"


def test_every_icon_the_build_copies_is_packaged() -> None:
    icons = {p.name for p in (ROOT / "addin" / "NavisCoord.Addin" / "Images").glob("*.png")}
    assert icons <= built_entries(), f"iconos sin empaquetar: {sorted(icons - built_entries())}"
