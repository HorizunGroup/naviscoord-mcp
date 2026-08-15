"""Which tag the marketplace manifests must name, and when.

A tag is a pointer to a commit, so whatever `ref` is written in that commit is
what the tag publishes forever. Bumping the ref after tagging produces a
a tag that installs the previous version, and nothing fails until somebody compares
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

The four phases, and what each one is allowed to assert:

* `dev`    — any other branch. The ref must be a tag that already exists.
* `pretag` — `release/naviscoord-X.Y.Z`. The ref must name the tag this commit
             is about to receive, which by definition does not exist yet.
* `main`   — the default branch, including the window between the release
             merge and the tag push. The ref must be either the declared
             version or an existing tag; a version whose tag has not landed is
             reported as that window, not as an error.
* `tag`    — a `refs/tags/` ref. Strictest: the tag must exist, must point at
             the commit being built, and must be the one the manifests name.

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
MAIN = "main"
TAG = "tag"
PHASES = (DEV, PRETAG, MAIN, TAG)

# The branch a release lands on before it is tagged. Named once, because the
# phase rule and the phase detection have to agree on it.
DEFAULT_BRANCH = "main"


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
    if candidate == DEFAULT_BRANCH:
        return MAIN
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
    tag_targets_head: bool | None = None,
) -> Verdict:
    """Whether the manifests name the right tag for this phase.

    `tags_available` distinguishes "this repository has no tags" from "nobody
    fetched them". The first is a legitimate state for a young repository; the
    second is a broken check, and it is reported as a failure rather than
    waved through — which is the specific bug this function exists to remove.

    `tag_targets_head` answers a different question that the tag phase used to
    conflate with the first: not "does the tag exist" but "is the tag the one
    being built". Both can be true of a checkout that is not the tag's commit,
    and only the second makes the run evidence about what the tag publishes.
    `None` means nobody looked, which in the tag phase is itself a failure —
    for the same reason a missing tag list is.
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
        if tag_targets_head is None:
            return Verdict(False, phase,
                           f"fase 'tag' pero no se pudo comprobar a qué commit apunta {want}: "
                           "haz checkout con tags (fetch-depth: 0 y fetch-tags: true).")
        if not tag_targets_head:
            return Verdict(False, phase,
                           f"el tag {want} existe pero NO apunta al commit del checkout, así que este run "
                           "no dice nada sobre lo que ese tag publica.")
        return Verdict(True, phase,
                       f"tag {want} publicado, apunta a este commit y los manifiestos lo nombran.")

    if phase == MAIN:
        # The default branch after a release merge and before the tag push.
        # That window is part of the process, not a mistake, so it is named
        # rather than failed — but it is the ONLY relaxation: the ref still has
        # to be either the version this commit declares or a tag that exists.
        if not tags_available:
            return Verdict(False, phase,
                           "fase 'main' pero no se pudieron leer los tags, y la regla habla justamente "
                           "de tags: haz checkout con tags (fetch-depth: 0 y fetch-tags: true).")
        if ref == want:
            if want in tags:
                return Verdict(True, phase, f"tag {want} publicado y main lo nombra.")
            return Verdict(True, phase,
                           f"ventana de release: main ya nombra {want}, que es lo que publicará el tag, "
                           "y ese tag todavía no se ha empujado.")
        if ref in tags:
            return Verdict(True, phase,
                           f"main mantiene el ref en el tag publicado {ref!r}; la versión declarada "
                           f"{version} todavía no se ha etiquetado.")
        return Verdict(False, phase,
                       f"main apunta a {ref!r}, que ni es un tag existente ({sorted(tags)}) ni la versión "
                       f"declarada ({want}).")

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


def tag_points_at_head(tag: str, root: Path = ROOT) -> bool | None:
    """Whether `tag` resolves to the commit currently checked out.

    `None` means the question could not be answered — git unavailable, or the
    tag not present — and callers must treat that as unknown rather than as a
    negative, because "no lo pude comprobar" and "no coincide" call for
    different messages.

    `rev-list -n1` is what peels an annotated tag down to its commit; comparing
    the tag OBJECT to HEAD would never match for annotated tags, which is every
    tag this project publishes.
    """
    def rev(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(root), *args],
                                 capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
        return out or None

    head = rev("rev-parse", "HEAD")
    target = rev("rev-list", "-n1", tag)
    if head is None or target is None:
        return None
    return head == target


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
    version = declared_version(root)

    # Only asked in the tag phase, and only when the tag is actually there:
    # anywhere else the answer is not part of the rule, and asking git for a
    # ref that does not exist would turn a clear "el tag no existe" into a
    # murkier "no se pudo comprobar".
    targets_head: bool | None = None
    if phase == TAG and expected_ref(version) in tags:
        targets_head = tag_points_at_head(expected_ref(version), root)

    return verdict(phase, version, declared_refs(root), tags,
                   tags_available=available, tag_targets_head=targets_head)


def main() -> int:
    parser = argparse.ArgumentParser(description="Comprueba el ref de los marketplaces.")
    parser.add_argument("--explain", action="store_true", help="detalla el estado leído")
    args = parser.parse_args()

    result = check()
    if args.explain:
        tags, available = local_tags()
        want = expected_ref(declared_version())
        print(f"fase      : {result.phase}")
        print(f"version   : {declared_version()}")
        print(f"refs      : {sorted(declared_refs())}")
        print(f"tags      : {sorted(tags) if available else '(no se pudieron leer)'}")
        print(f"rama      : {current_branch() or '(HEAD desacoplado)'}")
        print(f"HEAD      : {os.environ.get('GITHUB_SHA') or '(local)'}")
        if result.phase == TAG:
            points = tag_points_at_head(want) if want in tags else None
            print(f"{want} -> HEAD : "
                  + {True: "sí", False: "NO", None: "(no se pudo comprobar)"}[points])
    print(("ok  " if result.ok else "FALLA ") + result.detail)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
