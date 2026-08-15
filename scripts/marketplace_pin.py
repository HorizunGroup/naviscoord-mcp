"""Which tag the marketplace manifests must name, and when.

A tag is a pointer to a commit, so whatever `ref` is written in that commit is
what the tag publishes forever. Bumping the ref after tagging produces a
`v0.2.0` that installs `v0.1.2`, and nothing fails until somebody compares
versions by hand. Pointing at a tag that does not exist yet breaks every
install immediately. The correct value therefore depends on which phase the
repository is in — and the phase has to be *stated*, not guessed.

The previous version guessed, from `git rev-parse --abbrev-ref HEAD`, and the
guess could not work where it mattered most:

* CI checks out with no tags, so "are the refs real tags?" had no answer and
  the whole check quietly skipped — for every push, on every branch, since it
  was written;
* GitHub checks out a pull request at a detached HEAD, where that command
  answers `HEAD`, so the release-candidate phase was unreachable by
  construction.

A guard that skips is worse than no guard: it reports success. So the phase is
now taken from explicit signals, a missing prerequisite is an ERROR rather
than a skip, and the decision itself is a pure function that can be tested
against every phase without a repository at all.

    python scripts/marketplace_pin.py           # check this repository
    python scripts/marketplace_pin.py --explain # and say why
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MANIFESTS = (
    Path(".claude-plugin") / "marketplace.json",
    Path(".agents") / "plugins" / "marketplace.json",
)

# Phase names. Deliberately short and explicit: they are also the accepted
# values of NAVISCOORD_RELEASE_VALIDATION.
DEV = "dev"
PRETAG = "pretag"
TAG = "tag"
PHASES = (DEV, PRETAG, TAG)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    phase: str
    detail: str

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.ok


def detect_phase(env: dict[str, str], *, branch: str = "") -> str:
    """The release phase, from signals that survive a detached checkout.

    Order matters. An explicit override wins because a human running a release
    knows something the environment does not; after that a tag ref is
    unambiguous; only then does the branch name get a say — and it is read
    from `GITHUB_HEAD_REF` first, which is the *source* branch of a pull
    request and is populated precisely when `HEAD` is detached.
    """
    override = (env.get("NAVISCOORD_RELEASE_VALIDATION") or "").strip().lower()
    if override:
        if override not in PHASES:
            raise ValueError(
                f"NAVISCOORD_RELEASE_VALIDATION={override!r} no es válido; "
                f"usa uno de {', '.join(PHASES)}"
            )
        return override

    ref = env.get("GITHUB_REF") or ""
    if ref.startswith("refs/tags/"):
        return TAG

    # GITHUB_HEAD_REF is set only for pull_request events and holds the branch
    # the PR comes FROM. GITHUB_REF_NAME covers push events. The local branch
    # is the fallback for a developer's machine.
    candidate = (env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME") or branch or "").strip()
    if re.fullmatch(r"release/naviscoord-\d+\.\d+\.\d+", candidate):
        return PRETAG
    return DEV


def expected_ref(version: str) -> str:
    return f"v{version}"


def verdict(
    phase: str,
    version: str,
    refs: set[str],
    tags: set[str],
    *,
    tags_available: bool = True,
) -> Verdict:
    """Whether the manifests name the right tag for this phase.

    `tags_available` distinguishes "this repository has no tags" from "nobody
    fetched them". The first is a legitimate state for a young repository; the
    second is a broken check, and it is reported as a failure rather than
    waved through — which is the specific bug this function exists to remove.
    """
    if phase not in PHASES:
        return Verdict(False, phase, f"fase desconocida: {phase!r}")

    want = expected_ref(version)

    if len(refs) != 1:
        return Verdict(
            False, phase,
            f"los manifiestos declaran refs distintos entre sí ({sorted(refs)}); "
            "todos tienen que apuntar al mismo tag.",
        )
    (ref,) = tuple(refs)

    if phase == TAG:
        if not tags_available:
            return Verdict(False, phase,
                           "fase 'tag' pero no se pudieron leer los tags: haz checkout con tags "
                           "(fetch-depth: 0 y fetch-tags: true).")
        if want not in tags:
            return Verdict(False, phase,
                           f"fase 'tag' pero {want} no existe. Tags: {sorted(tags)}.")
        if ref != want:
            return Verdict(False, phase,
                           f"el tag {want} existe, así que los manifiestos deben apuntar a él y no a {ref!r}.")
        return Verdict(True, phase, f"tag {want} publicado y los manifiestos lo nombran.")

    if phase == PRETAG:
        # The one phase where naming a tag that does not exist yet is correct:
        # this commit is what will receive it.
        if ref != want:
            return Verdict(False, phase,
                           f"rama candidata de {want}: los manifiestos deben apuntar a {want} y no a {ref!r}. "
                           "El tag se creará sobre este commit y publicará el ref que este commit contenga.")
        return Verdict(True, phase,
                       f"rama candidata: los manifiestos ya nombran {want}, que es lo que el tag publicará.")

    # DEV: the tree carries the next version and the refs must keep pointing at
    # a tag that really exists.
    if not tags_available:
        return Verdict(False, phase,
                       "fase 'dev' pero no se pudieron leer los tags, y la regla es justamente "
                       "que el ref sea un tag EXISTENTE: haz checkout con tags.")
    if ref == want and want not in tags:
        return Verdict(False, phase,
                       f"los manifiestos apuntan a {want}, que todavía no existe, y esto no es la rama "
                       f"candidata. Crea la rama release/naviscoord-{version} o deja el ref en el último tag.")
    if ref not in tags:
        return Verdict(False, phase,
                       f"los manifiestos apuntan a {ref!r}, que no es un tag existente ({sorted(tags)}).")
    return Verdict(True, phase, f"desarrollo: el ref {ref!r} es un tag que existe.")


# ------------------------------------------------------------- repositorio


def declared_version(root: Path = ROOT) -> str:
    return json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def declared_refs(root: Path = ROOT) -> set[str]:
    refs: set[str] = set()
    for manifest in MANIFESTS:
        data = json.loads((root / manifest).read_text(encoding="utf-8"))
        for plugin in data["plugins"]:
            refs.add(plugin["source"]["ref"])
    return refs


def local_tags(root: Path = ROOT) -> tuple[set[str], bool]:
    """Tags and whether git could be consulted at all."""
    try:
        out = subprocess.run(["git", "-C", str(root), "tag"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return set(), False
    return set(out.split()), True


def current_branch(root: Path = ROOT) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""
    # Detached HEAD answers literally "HEAD", which names no branch.
    return "" if out == "HEAD" else out


def check(root: Path = ROOT, env: dict[str, str] | None = None) -> Verdict:
    env = dict(os.environ if env is None else env)
    tags, available = local_tags(root)
    phase = detect_phase(env, branch=current_branch(root))
    return verdict(phase, declared_version(root), declared_refs(root), tags,
                   tags_available=available)


def main() -> int:
    parser = argparse.ArgumentParser(description="Comprueba el ref de los marketplaces.")
    parser.add_argument("--explain", action="store_true", help="detalla el estado leído")
    args = parser.parse_args()

    result = check()
    if args.explain:
        tags, available = local_tags()
        print(f"fase      : {result.phase}")
        print(f"version   : {declared_version()}")
        print(f"refs      : {sorted(declared_refs())}")
        print(f"tags      : {sorted(tags) if available else '(no se pudieron leer)'}")
        print(f"rama      : {current_branch() or '(HEAD desacoplado)'}")
    print(("ok  " if result.ok else "FALLA ") + result.detail)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
