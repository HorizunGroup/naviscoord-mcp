"""Where this server is allowed to write.

Every output path in this codebase arrives as a tool argument, and a tool
argument is chosen by whatever is driving the MCP session — a model that just
read an untrusted clash export, in the normal case. Without a policy,
``navis_pdf_report(path=...)`` is an arbitrary-file-write primitive against
the user's machine, and ``navis_handoff(directory=...)`` is the same thing
with more files.

The policy is deliberately boring:

* writes land under an explicitly configured root, or under one safe default
  export directory;
* the path is resolved first and checked second, so ``..`` cannot climb out
  and a symlink cannot point out;
* UNC shares and device paths are refused outright — an attacker-chosen
  ``\\\\host\\share`` is an exfiltration channel, not a destination;
* existing files are never replaced unless the caller asked for it.

Configuration is an environment variable rather than a tool argument on
purpose. A policy the caller can widen is not a policy.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

ROOTS_ENV = "NAVISCOORD_OUTPUT_ROOTS"

# Reserved DOS device names. Still special on modern Windows regardless of
# extension or directory, and opening one is not a file write at all.
_DEVICE_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# NTFS alternate data stream: `report.pdf:hidden`. A colon anywhere except
# the drive-letter position means the caller is naming a stream, not a file.
_DRIVE_COLON = re.compile(r"^[A-Za-z]:[\\/]")


class PathPolicyError(RuntimeError):
    """Refusal to write somewhere, phrased for the person who asked."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {"error": "path_rejected", "detail": str(self)}
        if self.hint:
            payload["hint"] = self.hint
        return payload


def default_export_root() -> Path:
    """The one directory writable without any configuration at all.

    Under LOCALAPPDATA on Windows, under the XDG data dir elsewhere: the same
    place the session file and the bridge log already live, so a user who
    trusts the add-in is not being asked to trust a second location.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "NavisCoord" / "exports"


def configured_roots() -> list[Path]:
    """Allowed roots, most specific first. Always includes the default."""
    roots: list[Path] = []
    raw = os.environ.get(ROOTS_ENV, "")
    for chunk in raw.split(os.pathsep):
        candidate = chunk.strip().strip('"')
        if not candidate:
            continue
        try:
            roots.append(Path(candidate).expanduser().resolve())
        except OSError:
            # An unresolvable configured root is a configuration bug, not a
            # reason to fail every write: the default still applies.
            continue
    default = default_export_root()
    try:
        default = default.resolve()
    except OSError:
        pass
    if default not in roots:
        roots.append(default)
    return roots


@dataclass(frozen=True, slots=True)
class ResolvedOutput:
    path: Path
    root: Path
    existed: bool
    overwrite: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "allowed_root": str(self.root),
            "overwrote_existing": self.existed,
        }

    def confirm(self) -> None:
        """Cheap pre-check. Prefer :meth:`staged_write`, which cannot race.

        Kept for callers that want an early, friendly refusal before doing
        expensive work. On its own it is a check-then-write, which is exactly
        the pattern that loses a race — the guarantee comes from the exclusive
        reservation in :meth:`staged_write`.
        """
        if self.overwrite or self.existed:
            return
        if self.path.exists():
            raise PathPolicyError(
                f"«{self.path}» apareció mientras se preparaba la salida.",
                hint=(
                    "Otro proceso lo creó entre la autorización y la escritura. "
                    "Vuelve a llamar con overwrite=true si de verdad quieres reemplazarlo."
                ),
            )

    # ------------------------------------------------- atomic publication

    @contextmanager
    def staged_write(self) -> Iterator[Path]:
        """Yields a temp path to write into; publishes it atomically on exit.

        This is what actually closes the race, in two moves:

        1. **Reserve the name.** With ``overwrite=False`` the destination is
           created with ``O_CREAT|O_EXCL``, which is atomic at the filesystem
           level: of two processes racing for the same output exactly one
           succeeds and the other is refused. ``exists()`` followed by a write
           cannot promise that, however close together the two calls are.
        2. **Publish by rename.** The caller writes into a temp file in the
           SAME directory — same volume, so the rename is atomic — and
           ``os.replace`` swaps it into place in one step. A reader never sees
           a half-written report, and a failed generation leaves the
           destination untouched.

        On failure the temp file is removed, and so is a reservation this call
        created, so a crashed run does not leave a 0-byte file squatting on
        the name.
        """
        target = self.path
        reserved = False

        if not self.overwrite:
            try:
                # The atomic reservation. Losing here means somebody else won.
                handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                raise PathPolicyError(
                    f"«{target}» ya existe.",
                    hint=(
                        "Ya estaba, o otro proceso ganó la carrera por ese nombre. "
                        "Vuelve a llamar con overwrite=true si de verdad quieres reemplazarlo."
                    ),
                ) from exc
            except OSError as exc:
                raise PathPolicyError(f"No se pudo crear «{target}»: {exc}") from exc
            os.close(handle)
            reserved = True

        temp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            yield temp
            if not temp.exists():
                raise PathPolicyError(
                    f"La generación no escribió nada; «{target}» queda intacto."
                )
            # Atomic within the volume, and it replaces our own reservation.
            os.replace(temp, target)
        except BaseException:
            leftovers = [temp] + ([target] if reserved else [])
            for leftover in leftovers:
                try:
                    leftover.unlink()
                except OSError:
                    pass
            raise

    def write_bytes(self, data: bytes) -> None:
        """Writes bytes to the destination with the same guarantees."""
        with self.staged_write() as temp:
            temp.write_bytes(data)

    def write_text(self, text: str, encoding: str = "utf-8") -> None:
        with self.staged_write() as temp:
            temp.write_text(text, encoding=encoding)


class OutputPolicy:
    """Resolves and authorises an output path. Construct once, reuse."""

    def __init__(self, roots: list[Path] | None = None) -> None:
        self.roots = roots if roots is not None else configured_roots()
        # What the cache in `policy()` compares against, so a test that moves
        # the environment gets a fresh policy without an explicit reset.
        self.signature = os.environ.get(ROOTS_ENV, "")

    # ------------------------------------------------------------ checks

    def describe(self) -> dict[str, object]:
        return {
            "allowed_roots": [str(r) for r in self.roots],
            "env_var": ROOTS_ENV,
            "note": (
                "Las salidas solo se escriben bajo estas raíces. Para añadir otra, "
                f"define {ROOTS_ENV} (separadas por '{os.pathsep}') antes de arrancar el servidor."
            ),
        }

    def resolve_file(
        self, path: str | Path, *, overwrite: bool = False, create_parents: bool = True
    ) -> ResolvedOutput:
        """Authorise writing a single file, creating its parent if allowed."""
        resolved, root = self._authorise(path)

        if resolved.is_dir():
            raise PathPolicyError(
                f"«{resolved}» ya existe y es una carpeta, no un archivo.",
            )

        existed = resolved.exists()
        if existed and not overwrite:
            raise PathPolicyError(
                f"«{resolved}» ya existe.",
                hint="Vuelve a llamar con overwrite=true si de verdad quieres reemplazarlo.",
            )

        if create_parents:
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise PathPolicyError(
                    f"No se pudo crear la carpeta «{resolved.parent}»: {exc}"
                ) from exc

        return ResolvedOutput(path=resolved, root=root, existed=existed, overwrite=overwrite)

    def resolve_dir(self, path: str | Path, *, create: bool = True) -> ResolvedOutput:
        """Authorise writing several files into a directory."""
        resolved, root = self._authorise(path)
        existed = resolved.exists()
        if existed and not resolved.is_dir():
            raise PathPolicyError(f"«{resolved}» existe y no es una carpeta.")
        if create:
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise PathPolicyError(
                    f"No se pudo crear la carpeta «{resolved}»: {exc}"
                ) from exc
        return ResolvedOutput(path=resolved, root=root, existed=existed)

    # ----------------------------------------------------------- internals

    def _authorise(self, path: str | Path) -> tuple[Path, Path]:
        raw = str(path).strip()
        if not raw:
            raise PathPolicyError("La ruta de salida está vacía.")

        _reject_hostile_shape(raw)

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            # Relative paths are resolved against the first allowed root, so
            # `informe.pdf` works with no configuration at all.
            candidate = self.roots[0] / candidate

        try:
            # strict=False: the file does not exist yet, which is the point.
            # Symlinks in the existing prefix ARE followed, so a link planted
            # inside an allowed root cannot smuggle the write outside it.
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise PathPolicyError(f"No se pudo normalizar «{raw}»: {exc}") from exc

        _reject_hostile_shape(str(resolved))

        for root in self.roots:
            if _is_within(resolved, root):
                return resolved, root

        raise PathPolicyError(
            f"«{resolved}» está fuera de las rutas de salida permitidas.",
            hint=(
                "Permitidas: "
                + "; ".join(str(r) for r in self.roots)
                + f". Para autorizar otra carpeta, define {ROOTS_ENV} antes de arrancar el servidor."
            ),
        )


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_hostile_shape(raw: str) -> None:
    """Shapes that are never a legitimate local output file."""
    normalised = raw.replace("/", "\\")

    if normalised.startswith("\\\\"):
        # Covers both UNC (\\server\share) and the device namespaces
        # (\\.\PhysicalDrive0, \\?\C:\...), which bypass path normalisation.
        raise PathPolicyError(
            f"«{raw}» es una ruta de red o de dispositivo.",
            hint="Escribe en una carpeta local y copia después si necesitas publicarla.",
        )

    if "\x00" in raw:
        raise PathPolicyError("La ruta contiene un byte nulo.")

    # Colon outside the drive-letter position: an NTFS alternate data stream.
    tail = raw[2:] if _DRIVE_COLON.match(raw) else raw
    if ":" in tail:
        raise PathPolicyError(
            f"«{raw}» nombra un flujo alterno de datos (ADS), no un archivo.",
        )

    # PureWindowsPath, not PurePath: the separator was just normalised to
    # backslash, and on POSIX `PurePath` is `PurePosixPath`, which does not
    # treat backslash as a separator — so the whole path came back as ONE
    # part and no component ever matched a device name. The check silently
    # did nothing anywhere except Windows, which is also the only place the
    # suite ran until this branch reached CI. These are Windows names being
    # matched against a path bound for a Windows add-in, so the Windows
    # flavour is the right one on every platform.
    for part in PureWindowsPath(normalised).parts:
        stem = part.split(".")[0].strip().lower()
        if stem in _DEVICE_NAMES:
            raise PathPolicyError(
                f"«{part}» es un nombre de dispositivo reservado de Windows.",
            )


_DEFAULT_POLICY: OutputPolicy | None = None


def policy() -> OutputPolicy:
    """Process-wide policy, rebuilt only when the environment changes.

    Cached because `configured_roots` hits the filesystem to resolve each
    root, and the report writer authorises a path per artifact.
    """
    global _DEFAULT_POLICY
    signature = os.environ.get(ROOTS_ENV, "")
    if _DEFAULT_POLICY is None or _DEFAULT_POLICY.signature != signature:
        _DEFAULT_POLICY = OutputPolicy()
    return _DEFAULT_POLICY


def reset_policy() -> None:
    """Drops the cache. For tests that move the environment underneath it."""
    global _DEFAULT_POLICY
    _DEFAULT_POLICY = None
