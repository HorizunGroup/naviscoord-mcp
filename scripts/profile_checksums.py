"""Prints the canonical checksum of every profile this repository publishes.

The add-in pins these values in `VocabularyTests.ShippedProfileChecksums` so
that both halves are shown to agree on the FILES THAT SHIP, not only on a
synthetic vector. When a published profile is edited on purpose, its identity
changes on purpose too — and the literal in the C# test is regenerated from
here.

The direction matters and is not symmetric: Python is the source and the C#
literal follows. Adjusting the C# value until the two agree would turn a
contract test into a test that always passes.

    python scripts/profile_checksums.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from naviscoord.profile import checksum_of  # noqa: E402

PUBLISHED = (
    Path("server") / "naviscoord" / "profiles" / "default.json",
    Path("profiles") / "example-profile.json",
)


def main() -> int:
    problems = 0
    print("Perfiles publicados y su checksum canónico:\n")
    for rel in PUBLISHED:
        path = ROOT / rel
        if not path.is_file():
            print(f"FALTA  {rel}")
            problems += 1
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        schema = raw.get("$schema", "(sin declarar)")
        print(f"  {rel.as_posix()}")
        print(f"      $schema  : {schema}")
        print(f"      checksum : {checksum_of(raw)}")
    print(
        "\nPara el test de C#, copia estos valores a "
        "addin/NavisCoord.Tests/VocabularyTests.cs (ShippedProfileChecksums)."
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
