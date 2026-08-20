"""MCP server — `horizun-navis-mcp`.

Every tool is `navis_*`, and each one carries the join keys a coordination
issue needs to survive outside this process: the authoring tool's element
id, and — when the profile declares one — the budget code. That is what
lets an issue found here be fixed in the authoring model and costed in a
dashboard without anyone retyping it.

Two design rules shape this file:

**The heavy data never reaches the model.** A clash export is tens of
megabytes; it is fetched, analysed and cached in this process, and only a
ranked summary crosses into the conversation. Tools that would return
thousands of rows return a path to a file instead.

**Writes are two-step.** Every tool that modifies the document defaults to
`dry_run=True`, returning what it would touch. Committing is a separate,
explicit call, and the response reports what was verified by re-reading the
document — not what was attempted.
"""

from __future__ import annotations

import functools
import inspect
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - exercised by whichever mcp is present
    # mcp 1.x, where the same class was called FastMCP and lived elsewhere.
    # Both expose the identical surface this server uses — a name in the
    # constructor, a .tool() decorator that reads the signature and docstring,
    # and .run() defaulting to stdio — so supporting both is an import alias
    # rather than two code paths. Pinning to one would strand users on
    # whichever they already have.
    from mcp.server.fastmcp import FastMCP as _Server

from . import __version__
from .analysis import analyze, hotspots
from .bridge import BridgeError, CapabilityError
from .interop import write_handoff
from .model import ClashExport
from .paths import PathPolicyError
from .paths import policy as output_policy
from .profile import MAX_PROFILE_BYTES, Profile, canonical_text
from .sessions import TargetError
from .sessions import summary as session_summary
from .state import SessionState, StateError

def _server_with_version() -> Any:
    """Build the MCP server and stamp NavisCoord's version on the protocol.

    MCP 2 accepts a version in its high-level constructor; MCP 1 does not and
    leaves the low-level value at ``None``, which makes initialization fall
    back to the version of the *mcp dependency*.  That made NavisCoord 0.2.2
    introduce itself as, for example, 1.28.1.  Use each supported API and
    fail at import time if a future major removes both contracts instead of
    silently advertising the wrong product again.
    """
    parameters = inspect.signature(_Server).parameters
    server = (
        _Server("horizun-navis-mcp", version=__version__)
        if "version" in parameters
        else _Server("horizun-navis-mcp")
    )
    protocol = _protocol_server(server)
    if protocol is None or not hasattr(protocol, "version"):
        raise RuntimeError(
            "La biblioteca MCP instalada no permite declarar la versión de NavisCoord."
        )
    if getattr(protocol, "version", "") != __version__:
        try:
            protocol.version = __version__
        except (AttributeError, TypeError) as exc:
            raise RuntimeError(
                "La biblioteca MCP instalada rechazó la versión de NavisCoord."
            ) from exc
    return server


def _protocol_server(server: Any) -> Any:
    """Low-level protocol server under either supported MCP major."""
    for name in ("_mcp_server", "_lowlevel_server"):
        candidate = getattr(server, name, None)
        if candidate is not None:
            return candidate
    return server


mcp = _server_with_version()

# When this process imported its code. Anything on disk newer than this is a
# change the running server has not seen.
_BOOT_TIME = time.time()

STATE = SessionState()

# Kept in step with ProfileSchema.cs in the addin: one format, one version,
# read by both halves.
PROFILE_SCHEMA = "naviscoord.profile/v1"
# What a profile written before the format was versioned is read as.
LEGACY_PROFILE_SCHEMA = "naviscoord.profile/1"
ACCEPTED_PROFILE_SCHEMAS = (PROFILE_SCHEMA, LEGACY_PROFILE_SCHEMA)


def _version() -> str:
    return __version__


def _guard(fn):
    """Turns bridge failures into readable answers instead of stack traces.

    functools.wraps is load-bearing here, not tidiness. The MCP server builds
    each tool's JSON schema by inspecting the callable's signature, and a bare
    wrapper presents itself as (*args, **kwargs) — so every tool advertised a
    schema demanding two literal fields called "args" and "kwargs", and every
    call failed validation before reaching this code.

    The failure was invisible from the server side: the tools registered,
    listed and described themselves perfectly. Only calling one showed it.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CapabilityError as exc:
            # Before BridgeError, which it subclasses. The fix is different
            # from every other failure — update the addin, not retry — and a
            # caller that cannot tell them apart chases the wrong problem.
            payload = exc.to_json()
            payload["error"] = "capability_unavailable"
            payload["detail"] = str(exc)
            return payload
        except (BridgeError, PathPolicyError, StateError) as exc:
            # Each already carries a message meant for a human plus a hint.
            return exc.to_json()
        except TargetError as exc:
            return exc.to_json()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            return {"error": type(exc).__name__, "detail": str(exc)}

    return wrapper


# ---------------------------------------------------------------- session


@mcp.tool()
@_guard
def navis_health() -> dict[str, Any]:
    """Estado del puente y del documento abierto en Navisworks.

    Llama esto PRIMERO. Reporta la versión de Navisworks, qué documento está
    activo, cuántos modelos tiene la federación y cuántos tests y resultados
    de clash existen. Todo lo demás actúa sobre ese documento.

    Si hay varias instancias de Navisworks abiertas, responde la seleccionada
    con navis_target o, si no elegiste ninguna, la más reciente — y lo dice.
    """
    health = STATE.bridge.health()
    document = health.get("document") or {}
    fingerprint = str(health.get("document_fingerprint") or document.get("fingerprint") or "")
    changed = STATE.bind(fingerprint, str(document.get("title") or ""))

    health["profile"] = STATE.profile.name
    health["profile_checksum"] = STATE.profile_id
    health["analysis_in_memory"] = STATE.result is not None
    health["state"] = STATE.stamp()
    if changed:
        health["state_invalidated"] = (
            "El documento activo cambió desde el último análisis: descarté el resultado "
            "en memoria en vez de responder con datos de otro modelo."
        )
    health["targeting"] = session_summary()
    health["server"] = _staleness()
    return health


def _staleness() -> dict[str, Any]:
    """Whether this process is running the code that is on disk.

    The MCP server is a subprocess started once at session boot, so editing
    the Python leaves it running the old copy — silently. That has already
    produced two rounds of "the fix does not work" where the fix was fine and
    the process was stale. Comparing the loaded module's timestamp against
    the files makes it visible instead of a guessing game.
    """
    from . import __version__

    try:
        newest = max(f.stat().st_mtime for f in Path(__file__).parent.rglob("*.py"))
    except ValueError:
        return {"version": __version__}

    stale = newest > _BOOT_TIME
    return {
        "version": __version__,
        "code_newer_on_disk": stale,
        "note": (
            "Hay cambios en el código posteriores al arranque de este servidor: "
            "reinicia Claude Code para cargarlos."
            if stale
            else "El servidor corre el código que está en disco."
        ),
        "loaded_at": datetime.fromtimestamp(_BOOT_TIME).isoformat(timespec="seconds"),
        "newest_source": datetime.fromtimestamp(newest).isoformat(timespec="seconds"),
    }


@mcp.tool()
@_guard
def navis_load_profile(path: str = "") -> dict[str, Any]:
    """Carga un perfil de proyecto (pesos, tolerancias, reglas de disciplina).

    Sin argumento recarga el perfil por defecto. El perfil es la única fuente
    de criterio: cambiarlo cambia qué se considera grave, sin tocar código.

    El perfil se instala en LOS DOS lados: aquí y en el complemento, que lo
    recibe por el puerto autenticado y lo usa para `workflow/rules`,
    Configurar, los search sets y la matriz. Antes solo se cargaba aquí y el
    complemento seguía leyendo su propio archivo del disco, así que cargar un
    perfil y correr las reglas aplicaba criterios distintos sin decirlo.

    La respuesta trae `checksum`: el mismo valor a ambos lados identifica los
    criterios con los que se produjo un resultado.

    Cambiar de perfil INVALIDA el análisis en memoria: mueve severidad,
    tolerancias, reglas de disciplina y filtro de ruido a la vez, así que un
    resultado calculado con el anterior no está «algo desactualizado» —
    responde a otra pregunta.
    """
    profile = Profile.load(path or None)
    problems = profile.validate()
    schema = str(profile.raw.get("$schema") or profile.raw.get("schema") or "")
    notes: list[str] = []
    if schema and schema not in ACCEPTED_PROFILE_SCHEMAS:
        problems.append(
            f"Versión de esquema no soportada: «{schema}». Este servidor lee "
            + ", ".join(ACCEPTED_PROFILE_SCHEMAS)
            + "."
        )
    if not schema:
        # A warning, not a problem. Every profile written before the format
        # was versioned lacks this key, and refusing them meant an upgrade
        # broke every deployment in the field against profiles that were
        # otherwise valid. A MISSING version migrates; an UNKNOWN one still
        # fails, because that is the case nobody can migrate.
        notes.append(
            f'El perfil no declara "$schema", así que se lee como {LEGACY_PROFILE_SCHEMA} '
            f'(el formato anterior a versionar). Añade "$schema": "{PROFILE_SCHEMA}" '
            "al inicio para fijarlo explícitamente."
        )

    if problems:
        # A profile that does not validate is not installed: swapping in a
        # broken one and reporting the problems separately is how a run ends
        # up scored by criteria nobody chose.
        return {
            "ok": False,
            "profile": profile.name,
            "schema": schema,
            "accepted_schemas": list(ACCEPTED_PROFILE_SCHEMAS),
            "problems": problems,
            "note": "El perfil NO se cargó: sigue vigente el anterior.",
            "active_profile": STATE.profile.name,
            "active_checksum": STATE.profile_id,
        }

    canonical = canonical_text(profile.raw)
    size = len(canonical.encode("utf-8"))
    if size > MAX_PROFILE_BYTES:
        return {
            "ok": False,
            "profile": profile.name,
            "problems": [
                f"El perfil ocupa {size} bytes y el máximo es {MAX_PROFILE_BYTES}. "
                "Un perfil es un archivo de criterios escrito a mano; algo así de grande "
                "suele ser un archivo equivocado."
            ],
            "note": "El perfil NO se cargó: sigue vigente el anterior.",
            "active_profile": STATE.profile.name,
            "active_checksum": STATE.profile_id,
        }

    changed = STATE.set_profile(profile)

    # Pushed to the addin so both ends judge by the same criteria. A failure
    # here is REPORTED, not swallowed: a caller who thinks the addin took
    # their profile and gets the plugin's default instead is exactly the
    # confusion this whole path exists to remove.
    addin: dict[str, Any] = {"synchronised": False}
    try:
        pushed = STATE.bridge.profile_load(canonical, STATE.profile_id)
        addin = {
            "synchronised": bool(pushed.get("ok")),
            "checksum": pushed.get("checksum", ""),
            "source": pushed.get("source", ""),
            "session_id": pushed.get("session_id", ""),
        }
        if not pushed.get("ok"):
            addin["error"] = pushed.get("error", "")
            addin["detail"] = pushed.get("detail", "")
            addin["problems"] = pushed.get("problems", [])
        elif pushed.get("checksum") != STATE.profile_id:
            # Both ends validated the same bytes and disagreed about their
            # identity: that is a canonicaliser drifting, and it must not be
            # rounded off to a warning.
            addin["synchronised"] = False
            addin["error"] = "checksum_mismatch"
            addin["detail"] = (
                f"El servidor calculó {STATE.profile_id} y el complemento "
                f"{pushed.get('checksum')} sobre el mismo contenido."
            )
    except BridgeError as exc:
        # No Navisworks connected is a normal state — the profile is loaded
        # here and will be pushed on the next connection.
        addin = {"synchronised": False, "error": "bridge_unavailable", "detail": str(exc)}

    return {
        "ok": True,
        "profile": profile.name,
        "schema": schema or LEGACY_PROFILE_SCHEMA,
        "warnings": notes,
        "checksum": STATE.profile_id,
        "bytes": size,
        "disciplines": sorted(profile.disciplines),
        "problems": [],
        "invalidated_analysis": changed,
        "addin": addin,
        "note": _load_note(changed, addin),
    }


def _load_note(changed: bool, addin: dict[str, Any]) -> str:
    if not addin.get("synchronised"):
        return (
            "El perfil quedó activo en el servidor, pero NO se pudo instalar en el "
            f"complemento ({addin.get('detail') or addin.get('error') or 'sin detalle'}). "
            "Los pasos que corren dentro de Navisworks seguirán usando el perfil del "
            "complemento; revisa 'addin' antes de fiarte del resultado."
        )
    if changed:
        return (
            "El perfil cambió en ambos lados: descarté el análisis en memoria y los "
            "overrides de disciplina."
        )
    return "Perfil recargado sin cambios de criterio, y sincronizado con el complemento."


@mcp.tool()
@_guard
def navis_reset_profile() -> dict[str, Any]:
    """Devuelve el complemento a su perfil por defecto (el del disco).

    Deshace lo que hizo `navis_load_profile` en el complemento: a partir de
    aquí los pasos que corren dentro de Navisworks vuelven a usar el archivo
    que encuentre en disco, reportado como `plugin_default`. Útil para volver
    al perfil corporativo de la máquina tras probar uno alternativo.

    El servidor sigue con el perfil que tenga cargado; `navis_profile_info`
    muestra los dos lados.
    """
    result = STATE.bridge.profile_reset()
    result["server_profile"] = STATE.profile.name
    result["server_checksum"] = STATE.profile_id
    return result


# --------------------------------------------------------------- targeting


@mcp.tool()
@_guard
def navis_sessions() -> dict[str, Any]:
    """Instancias de Navisworks activas, con su documento y su target_id.

    Un coordinador tiene a menudo dos abiertas —2024 con el modelo archivado
    y 2026 con el vivo, o dos proyectos a la vez—. Cada una publica su propia
    sesión: aquí se ven todas, con cuál está seleccionada.
    """
    payload = session_summary()
    payload["selected_target"] = STATE.bridge.target_id or ""
    return payload


@mcp.tool()
@_guard
def navis_target(target_id: str) -> dict[str, Any]:
    """Fija a qué instancia de Navisworks van las llamadas siguientes.

    Obligatorio para mutar cuando hay más de una abierta: elegir en silencio
    la última es cómo un plan calculado sobre Torre A termina escrito en
    Torre B. Los ids salen de navis_sessions.
    """
    session = STATE.bridge.pin(target_id)
    # A different instance means a different document: everything derived
    # from the previous one goes.
    STATE.forget_derived()
    STATE.target_id = session.target_id
    STATE.bind(session.document_fingerprint, session.document_title, session.target_id)
    return {
        "selected": session.to_json(),
        "note": "Descarté el análisis en memoria: pertenecía a la instancia anterior.",
    }


@mcp.tool()
@_guard
def navis_current_target() -> dict[str, Any]:
    """Qué instancia y qué documento están seleccionados ahora mismo."""
    session = STATE.bridge.resolve()
    return {
        "target": session.to_json(),
        "pinned": bool(STATE.bridge.target_id),
        "state": STATE.stamp(),
        "note": (
            "Target fijado explícitamente."
            if STATE.bridge.target_id
            else "Sin target fijado: se resuelve en cada llamada y una mutación con varias "
                 "instancias abiertas será rechazada."
        ),
    }


@mcp.tool()
@_guard
def navis_capabilities(refresh: bool = False) -> dict[str, Any]:
    """Qué sabe hacer el complemento instalado, antes de pedírselo.

    Versión de contrato, rutas disponibles, operaciones soportadas, versión de
    Navisworks, si puede guardar y guardar como, si admite trabajos asíncronos
    y qué versión de esquema de perfil lee.

    El servidor MCP y el complemento se actualizan por separado, así que
    consultar esto es lo que convierte «unknown_route» en «actualiza el
    complemento a esta versión».
    """
    capabilities = STATE.bridge.capabilities(refresh=refresh)
    capabilities["server"] = {
        "version": _version(),
        "profile_schema": PROFILE_SCHEMA,
        "accepted_profile_schemas": list(ACCEPTED_PROFILE_SCHEMAS),
        "output_policy": output_policy().describe(),
    }
    return capabilities


# ------------------------------------------------------------ preparation


@mcp.tool()
@_guard
def navis_model_census() -> dict[str, Any]:
    """Inventario de la federación: archivos, tamaños e histograma de categorías.

    Úsalo antes de armar la matriz de tests. El histograma de categorías por
    archivo es lo que permite decidir la disciplina de cada modelo con
    evidencia en lugar de adivinar por el nombre del archivo.
    """
    return STATE.bridge.census()


@mcp.tool()
@_guard
def navis_propose_disciplines() -> dict[str, Any]:
    """Propone el mapeo archivo → disciplina, para revisión humana.

    No aplica nada. Un mapeo equivocado envenena en silencio toda la
    priorización posterior y termina culpando al equipo incorrecto, así que
    la propuesta se confirma antes de usarse.
    """
    census = STATE.bridge.census()
    profile = STATE.profile
    proposals = []

    for model in census.get("models", []):
        source = model.get("source_file", "")
        by_name = profile.discipline_for_filename(source)

        votes: dict[str, int] = {}
        for category, count in (model.get("top_categories") or {}).items():
            code = profile.discipline_for_category(category)
            if code:
                votes[code] = votes.get(code, 0) + int(count)

        by_category = max(votes, key=votes.get) if votes else None
        total = sum(votes.values()) or 1
        share = votes.get(by_category, 0) / total if by_category else 0.0

        proposals.append(
            {
                "source_file": source,
                "by_filename": by_name or "",
                "by_category": by_category or "",
                "category_agreement": round(share, 2),
                "proposed": by_category or by_name or "OTRO",
                "agrees": bool(by_name and by_category and by_name == by_category),
                "item_count": model.get("item_count", 0),
            }
        )

    conflicts = [p for p in proposals if p["by_filename"] and p["by_category"] and not p["agrees"]]
    return {
        "proposals": proposals,
        "conflicts": conflicts,
        "note": (
            "Confirma o corrige con navis_set_disciplines antes de construir la matriz."
            if conflicts
            else "Nombre de archivo y categorías coinciden en todos los modelos."
        ),
    }


@mcp.tool()
@_guard
def navis_set_disciplines(mapping: dict[str, str]) -> dict[str, Any]:
    """Fija el mapeo archivo → disciplina, sobrescribiendo la heurística.

    mapping: {"PROY-HVAC-T1.rvt": "HVAC", ...}
    """
    STATE.overrides = {k: v for k, v in mapping.items()}
    return {"overrides": STATE.overrides, "note": "Se aplicarán en el próximo navis_analyze."}


# ------------------------------------------------- envelope de las rutas crudas

#: Routes that mutate but answer with their own payload instead of the
#: addin's `MutationResult`. The envelope is assembled here from what the
#: document says afterwards, never from what the call claimed.
CONTRACT = "naviscoord.mutation/1"


def _status_for(*, dry_run: bool, requested: int, applied: int, verified: int,
                failed: int, errors: list[str]) -> str:
    """The shared status rule, kept identical to the addin's.

    `MutationContract.cs::Status()` is the source of truth; this mirrors it so
    a caller cannot tell whether a given operation happened to travel through
    the C# envelope or this one. Any change belongs in both, and the contract
    test compares the two tables rather than trusting this comment.
    """
    if dry_run:
        return "planned"
    if errors and verified == 0:
        return "failed"
    if applied == 0 and requested == 0:
        return "completed"
    if verified == 0 and applied > 0:
        return "failed"
    if verified < applied or failed > 0 or applied < requested:
        return "partial"
    return "completed"


def _envelope(
    operation: str,
    raw: dict[str, Any],
    *,
    dry_run: bool,
    requested: int,
    applied: int,
    verified: int,
    failed: int,
    fingerprint: str,
    idempotency_key: str,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    **detail: Any,
) -> dict[str, Any]:
    """Wraps a raw route answer in the uniform mutation envelope.

    Field names copy the addin's exactly. The counts are the caller's
    responsibility to derive from a re-read; nothing here inspects how many
    calls succeeded, because a call that did not throw is not evidence that
    the document changed.
    """
    warn = list(warnings or [])
    err = list(errors or [])
    payload: dict[str, Any] = {
        "operation": operation,
        "contract": CONTRACT,
        "job_id": str(raw.get("job_id") or ""),
        "target_id": STATE.bridge.target_id,
        "idempotency_key": idempotency_key,
        "document_fingerprint_before": fingerprint,
        "document_fingerprint_after": raw.get("document_fingerprint_after") or fingerprint,
        "dry_run": dry_run,
        "requested": requested,
        "applied": applied,
        "verified": verified,
        "failed": failed,
        "status": _status_for(
            dry_run=dry_run, requested=requested, applied=applied,
            verified=verified, failed=failed, errors=err,
        ),
        "verification_source": "not_applicable" if dry_run else "document_reread",
        "warnings": warn,
        "errors": err,
    }
    payload.update(detail)
    return payload


@mcp.tool()
@_guard
def navis_list_search_sets() -> dict[str, Any]:
    """Lista los conjuntos de selección del documento. No modifica nada.

    Es la lectura con la que se comprueba lo que `navis_build_search_sets`
    dejó escrito, y la única de este grupo que no exige huella ni destino
    para mutar: sin escritura no hay nada que proteger.

    `item_count` solo es real para conjuntos explícitos. Un search set guarda
    la regla, no la lista, así que Navisworks no reporta un tamaño hasta
    reevaluarla — se devuelve `null` en vez de un cero que se leería como
    «vacío».
    """
    session = STATE.bridge.resolve()
    raw = STATE.bridge.list_sets()

    sets: list[dict[str, Any]] = []
    for entry in raw.get("sets") or []:
        is_group = bool(entry.get("is_group"))
        declared_kind = str(entry.get("kind") or "")
        count = entry.get("item_count")
        explicit = declared_kind == "explicit" or (
            not declared_kind and not is_group and isinstance(count, (int, float))
            and not isinstance(count, bool) and int(count) > 0
        )
        sets.append(
            {
                "name": entry.get("name") or "",
                "guid": entry.get("guid") or "",
                "kind": "folder" if is_group else ("explicit" if explicit else "search"),
                "item_count": int(count) if explicit else None,
            }
        )

    warnings: list[str] = []
    unsized = sum(1 for s in sets if s["kind"] == "search")
    if unsized:
        warnings.append(
            f"{unsized} conjunto(s) guardan una regla, no una lista: su tamaño solo se "
            "conoce reevaluándola en el modelo, así que no se reporta."
        )

    return {
        "contract": CONTRACT,
        "target_id": session.target_id,
        "document_title": session.document_title,
        "document_fingerprint": session.document_fingerprint,
        "count": len(sets),
        "sets": sets,
        "warnings": warnings,
    }


@mcp.tool()
@_guard
def navis_build_search_sets(
    folders: list[dict[str, Any]],
    dry_run: bool = True,
    replace_existing: bool = True,
    observe_categories: bool = False,
    run_async: bool = False,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Crea carpetas de search sets definidos por criterio, no por lista.

    Complementa `navis_build_sets`, no lo reemplaza: aquel congela QUÉ
    elementos se probaron, para que dos corridas de la matriz sigan siendo
    comparables; éste guarda la REGLA, de modo que la plantilla sobreviva a
    una actualización del federado. El ancla recomendada es independiente del
    idioma —alcance por modelo y `CategoryId` numérico— porque el nombre de
    categoría cambia con el idioma del Revit que publicó.

    `folders`: [{"folder": "MEP", "scope_model_contains": "-MEP-",
                 "sets": [{"name": "Tubería", ...criterios}]}]

    Con `replace_existing=False` una carpeta que ya existe se respeta en vez
    de recrearse: los clash tests la referencian, y recrearla rompería el
    enlace y borraría resultados corridos.
    """
    if not folders:
        return {
            "error": "argumento_invalido",
            "detail": "Se requiere 'folders' con al menos una carpeta de disciplina.",
        }

    STATE.bridge.require("sets/build_search")
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    payload = {
        "folders": folders,
        "dry_run": dry_run,
        "replace_existing": replace_existing,
        "observe_categories": observe_categories,
        "expected_document_fingerprint": fingerprint,
        "idempotency_key": idempotency_key,
    }

    # The before-state comes from the document, so "created" and "updated" can
    # be told apart honestly instead of both being reported as "written".
    before = {
        s.get("name") or "" for s in (STATE.bridge.list_sets().get("sets") or [])
    } if not dry_run else set()

    raw = STATE.bridge.run_route("sets/build_search", payload, run_async=run_async)
    if raw.get("job_id") and raw.get("state") in (None, "queued", "running"):
        return {"job_id": raw["job_id"], "state": raw.get("state") or "queued",
                "operation": "sets/build_search", "contract": CONTRACT,
                "note": "Consulta navis_job_status para el envelope final."}

    planned = raw.get("planned") or []
    requested = sum(len(p.get("sets") or []) for p in planned if isinstance(p, dict))
    errors = [
        f"{p.get('folder')}: {p.get('error')}"
        for p in planned
        if isinstance(p, dict) and p.get("error")
    ]

    verified_rows = raw.get("verified_in_document") or []
    expected_by_folder = {
        str(row.get("folder") or ""): {
            str(item.get("name") or "")
            for item in (row.get("sets") or [])
            if isinstance(item, dict) and item.get("name")
        }
        for row in planned if isinstance(row, dict)
    }
    actual_by_folder = {
        str(row.get("folder") or ""): {
            str(name) for name in (row.get("children") or []) if name
        }
        for row in verified_rows if isinstance(row, dict) and row.get("exists")
    }
    verified = sum(
        len(expected & actual_by_folder.get(folder, set()))
        for folder, expected in expected_by_folder.items()
    )
    present = {row.get("folder") for row in verified_rows if row.get("exists")}
    missing = [
        row.get("folder") for row in verified_rows if not row.get("exists")
    ]
    missing_sets = [
        f"{folder}/{name}"
        for folder, expected in expected_by_folder.items()
        for name in sorted(expected - actual_by_folder.get(folder, set()))
    ]

    created = sorted(present - before)
    touched = sorted(present & before)
    return _envelope(
        "sets/build_search",
        raw,
        dry_run=dry_run,
        requested=requested,
        applied=0 if dry_run else int(raw.get("applied", verified)),
        verified=verified,
        failed=len(errors) + len(missing) + len(missing_sets),
        fingerprint=fingerprint,
        idempotency_key=idempotency_key,
        errors=(errors + [f"{f}: no quedó en el documento" for f in missing]
                + [f"{name}: el set solicitado no quedó en el documento" for name in missing_sets]),
        created=created if not dry_run else [],
        updated=touched if (not dry_run and replace_existing) else [],
        unchanged=touched if (not dry_run and not replace_existing) else [],
        precedence=(
            "alcance por modelo (scope_model_contains) y luego los criterios de cada set; "
            "el modo que ganó por set viaja en planned[].sets[].match_mode"
        ),
        planned=planned,
        observed_categories=raw.get("observed_categories"),
    )


@mcp.tool()
@_guard
def navis_apply_clash_rules(
    pairs: list[dict[str, Any]],
    sets_index: dict[str, Any],
    status: str = "Approved",
    only_new: bool = True,
    dry_run: bool = True,
    run_async: bool = False,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Marca con un estado los cruces cuyos dos lados casan un par a ignorar.

    Es el triaje explícito: el llamador nombra los pares y sus conjuntos, a
    diferencia de `navis_run_rules_workflow`, que los deriva del perfil.

    `sets_index`: {"EST": {"scope_model_contains": "-EST-",
                           "category_ids": ["-2000011"]}, ...}
    `pairs`: [{"test": "EST vs HID", "a": "EST", "b": "HID"}]

    Un par casa en cualquiera de los dos sentidos. Con `only_new=True` solo
    toca los cruces en estado New, que es lo que hace la repetición inocua:
    lo ya aprobado no se vuelve a tocar.
    """
    if not pairs or not sets_index:
        return {
            "error": "argumento_invalido",
            "detail": "Se requieren 'pairs' y 'sets_index'; sin ambos no hay regla que aplicar.",
        }
    unknown = sorted(
        {side for p in pairs for side in (p.get("a"), p.get("b")) if side not in sets_index}
    )
    if unknown:
        return {
            "error": "argumento_invalido",
            "detail": f"Los pares nombran conjuntos ausentes de sets_index: {', '.join(unknown)}.",
        }

    STATE.bridge.require("clash/apply_rules")
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    payload = {
        "pairs": pairs,
        "sets_index": sets_index,
        "status": status,
        "only_new": only_new,
        "dry_run": dry_run,
        "expected_document_fingerprint": fingerprint,
        "idempotency_key": idempotency_key,
    }
    raw = STATE.bridge.run_route("clash/apply_rules", payload, run_async=run_async)
    if raw.get("job_id") and raw.get("state") in (None, "queued", "running"):
        return {"job_id": raw["job_id"], "state": raw.get("state") or "queued",
                "operation": "clash/apply_rules", "contract": CONTRACT,
                "note": "Consulta navis_job_status para el envelope final."}

    per_test = raw.get("tests") or []
    evaluated = sum(int(t.get("results_total") or 0) for t in per_test)
    matched = int(raw.get("matched") or 0)
    changed = int(raw.get("edited") or 0)

    checked = raw.get("verified_in_document") or {}
    verified = int(raw.get("verified_edited") or 0)
    mismatch = sum(int(v.get("mismatch") or 0) for v in checked.values())

    by_rule: dict[str, int] = {}
    for entry in per_test:
        for rule, hits in (entry.get("by_rule") or {}).items():
            by_rule[rule] = by_rule.get(rule, 0) + int(hits or 0)

    warnings: list[str] = []
    if matched and not changed and not dry_run:
        warnings.append(
            f"{matched} cruce(s) casaron pero ninguno aceptó el cambio de estado."
        )
    errors = [f"{t.get('test')}: {t.get('error')}" for t in per_test if t.get("error")]

    return _envelope(
        "clash/apply_rules",
        raw,
        dry_run=dry_run,
        requested=matched,
        applied=changed,
        verified=verified,
        failed=mismatch + len(errors),
        fingerprint=fingerprint,
        idempotency_key=idempotency_key,
        warnings=warnings,
        errors=errors,
        evaluated=evaluated,
        matched=matched,
        status_changed=changed,
        mismatch=mismatch,
        status_applied=raw.get("status_applied") or status,
        only_new=only_new,
        rules_applied=by_rule,
        per_test=per_test,
        verified_in_document=checked,
    )


@mcp.tool()
@_guard
def navis_run_rules_workflow(
    run_async: bool = True,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Paso 4 — aplica las reglas residuales que declara el PERFIL.

    Compone dos cosas y nada más: corre todos los tests **solo si** el bloque
    de reglas del perfil lo pide con `run_tests`, y luego aplica esos pares
    con `clash/apply_rules`. No agrupa, no colorea y no vuelve a construir
    sets; si ya corriste los tests, deja `run_tests` en falso en el perfil
    para no repetirlos.

    La diferencia con `navis_apply_clash_rules` es de dónde salen los pares:
    aquí del perfil, allí del llamador. Con un perfil sin bloque de reglas el
    paso no es un error — devuelve «nada que aprobar», que con el estándar
    actual es el estado normal, porque las exclusiones ya viven en las
    selecciones de cada test.

    **El perfil que usa este paso es el que tengas cargado.** Si lo enviaste
    con `navis_load_profile`, ese es; si no, el complemento usa el suyo de
    disco y lo declara como `plugin_default`. La respuesta trae
    `profile_checksum` y `profile_source`, tanto en la vía síncrona como en el
    resultado del job, así que se puede comprobar en vez de suponerlo.

    Antes este paso leía SIEMPRE el archivo del complemento e ignoraba
    en silencio el perfil cargado por MCP. `navis_profile_info` responde de un
    vistazo si los dos lados coinciden.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    out = _workflow(
        "rules",
        run_async,
        {
            "expected_document_fingerprint": fingerprint,
            "idempotency_key": idempotency_key,
        },
    )
    return out


@mcp.tool()
@_guard
def navis_build_sets(
    dry_run: bool = True,
    prefix: str = "NC",
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Crea un conjunto de selección explícito por disciplina.

    Conjuntos explícitos y no de búsqueda, a propósito: un search set se
    reevalúa cuando cambia el modelo, y entonces dos corridas de la matriz
    dejan de ser comparables. Un conjunto explícito es una declaración
    congelada y auditable de qué se probó.

    Con dry_run=True (por defecto) solo reporta cuántos elementos caerían en
    cada disciplina.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    disciplines = []
    for code, rule in STATE.profile.disciplines.items():
        if code == "OTRO":
            continue
        sources = [f for f, d in STATE.overrides.items() if d == code]
        disciplines.append(
            {
                "discipline": code,
                "categories": sorted(rule.categories),
                "source_files": sources,
            }
        )
    return STATE.bridge.build_sets(
        disciplines, prefix, dry_run, fingerprint=fingerprint,
        idempotency_key=idempotency_key,
    )


@mcp.tool()
@_guard
def navis_build_clash_matrix(
    dry_run: bool = True, prefix: str = "NC", replace_existing: bool = False,
    expected_document_fingerprint: str = "", idempotency_key: str = "",
) -> dict[str, Any]:
    """Genera la suite completa de tests disciplina contra disciplina.

    Cada par recibe el tipo (Hard / HardConservative / Clearance) y la
    tolerancia que dicta el perfil — estructura contra instalaciones a 1 mm,
    holguras de mantenimiento a 50 mm. Requiere navis_build_sets antes.
    """
    pairs = STATE.profile.section("clash_matrix").get("pairs", [])
    if not pairs:
        return {"error": "El perfil no define clash_matrix.pairs."}
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    return STATE.bridge.build_matrix(
        pairs, prefix, dry_run, replace_existing,
        fingerprint=fingerprint, idempotency_key=idempotency_key,
    )


@mcp.tool()
@_guard
def navis_list_tests() -> dict[str, Any]:
    """Lista los tests de clash del documento con su estado y conteo."""
    return STATE.bridge.list_tests()


@mcp.tool()
@_guard
def navis_run_tests(
    tests: list[str] | None = None,
    dry_run: bool = True,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Corre los tests indicados, o todos si no se especifica ninguno.

    Reporta el conteo de resultados antes y después de cada test, para que un
    test que no encontró nada se distinga de uno que no llegó a correr.

    Correr un test escribe sus resultados dentro del documento, así que pasa
    por el mismo guardia que el resto de las mutaciones aunque no tenga
    dry_run: no hay ensayo posible de una corrida de clash.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    if dry_run:
        available = STATE.bridge.list_tests().get("tests") or []
        wanted = {name.lower() for name in (tests or [])}
        selected = [
            row.get("name") for row in available
            if not wanted or str(row.get("name") or "").lower() in wanted
        ]
        return {
            "operation": "clash/run", "status": "planned", "dry_run": True,
            "document_fingerprint_before": fingerprint,
            "requested": len(selected), "applied": 0, "verified": 0,
            "tests": selected,
        }
    return STATE.bridge.run_tests(
        tests, fingerprint=fingerprint, idempotency_key=idempotency_key,
    )


# ------------------------------------------------------------- analysis


@mcp.tool()
@_guard
def navis_analyze(tests: list[str] | None = None, limit: int = 0, top: int = 15) -> dict[str, Any]:
    """Extrae los cruces de Navisworks y corre el motor de coordinación.

    Este es el comando central. Filtra ruido, colapsa cruces redundantes en
    problemas reales, puntúa cada uno con justificación, detecta causas raíz
    sistémicas y pliega los problemas que una sola decisión cierra.

    Devuelve un resumen compacto — nunca la lista cruda — con los problemas
    mejor puntuados y las causas raíz. El resultado completo queda en memoria
    para navis_issue_detail, navis_apply_groups y el resto.
    """
    properties = list(STATE.profile.section("interop").get("harvest_properties", []))
    # The discovered key has to travel with the export or the tagger cannot
    # see it. Assumed lists never include a property nobody knew existed.
    if STATE.group_key and STATE.group_key not in properties:
        properties.append(STATE.group_key)

    # Bind BEFORE analysing: the result must record which document it came
    # from, so a later tool can refuse to answer about a different one.
    fingerprint, title = STATE.live_fingerprint()
    STATE.bind(fingerprint, title)

    raw = STATE.bridge.export_clashes(properties=properties, tests=tests, limit=limit)

    export = ClashExport.from_json(raw)
    result = analyze(
        export,
        STATE.profile,
        discipline_overrides=STATE.overrides,
        group_key=STATE.group_key,
        group_roles=STATE.group_roles,
    )

    STATE.export = export
    STATE.result = result

    summary = result.summary(limit=top)
    summary["provenance"] = STATE.stamp()
    if raw.get("truncated"):
        summary.setdefault("warnings", []).append(
            f"La extracción se truncó en {limit} cruces: el análisis es parcial."
        )
    return summary


@mcp.tool()
@_guard
def navis_issue_detail(issue_id: str) -> dict[str, Any]:
    """Detalle completo de un problema: elementos, propiedades, por qué puntúa así."""
    issue = STATE.issue(issue_id)
    export = STATE.export
    payload = issue.to_json()

    elements: list[dict[str, Any]] = []
    if export is not None:
        index = {c.a.path_id: c.a for c in export.clashes}
        index.update({c.b.path_id: c.b for c in export.clashes})
        for side, paths in (("a", issue.elements_a), ("b", issue.elements_b)):
            for path in paths:
                element = index.get(path)
                if element is None:
                    continue
                elements.append(
                    {
                        "side": side,
                        "path_id": element.path_id,
                        "name": element.display_name,
                        "category": element.category,
                        "discipline": element.discipline,
                        "source_file": element.source_file,
                        "props": element.props,
                    }
                )

    payload["elements"] = elements
    payload["clash_guids"] = issue.clash_ids
    return payload


@mcp.tool()
@_guard
def navis_root_causes(limit: int = 10) -> dict[str, Any]:
    """Patrones sistémicos detectados: cota errada, pasos faltantes, detalle repetido, zonas saturadas.

    Esto es lo que comprime un ciclo de coordinación. Una causa raíz con
    cuarenta problemas debajo se cierra con una decisión, no con cuarenta.

    Devuelve las `limit` más grandes más un conteo por tipo. Un modelo real
    genera decenas de causas del mismo tipo —48 sistemas mal trazados, 19
    zonas saturadas— y volcarlas todas son 61 KB de texto casi repetido.
    """
    result = STATE.require_fresh_result()
    causes = sorted(result.root_causes, key=lambda c: (-c.clash_count, -c.confidence))

    by_kind: dict[str, dict[str, int]] = {}
    for cause in causes:
        entry = by_kind.setdefault(cause.kind, {"causes": 0, "clashes": 0, "issues": 0})
        entry["causes"] += 1
        entry["clashes"] += cause.clash_count
        entry["issues"] += len(cause.affected_clusters)

    return {
        "total_causes": len(causes),
        "by_kind": by_kind,
        "top": [cause.to_json() for cause in causes[:limit]],
        "issues": len(result.issues),
        "decisions": len(result.decisions),
        "note": (
            f"Mostrando {min(limit, len(causes))} de {len(causes)}. "
            "El resumen por tipo cubre todas."
        ),
    }


@mcp.tool()
@_guard
def navis_hotspots(cell_size_m: float = 5.0, limit: int = 10) -> dict[str, Any]:
    """Zonas del modelo con más severidad acumulada, para recorrerlas en orden."""
    result = STATE.require_fresh_result()
    return {"hotspots": hotspots(result, cell_size=cell_size_m, limit=limit)}


@mcp.tool()
@_guard
def navis_decisions(limit: int = 20, priority: str = "") -> dict[str, Any]:
    """Los problemas a atacar, ya sin los que otra decisión cierra.

    Filtra opcionalmente por prioridad: critical, high, medium, low.
    """
    result = STATE.require_fresh_result()
    issues = result.decisions
    if priority:
        issues = [i for i in issues if i.priority == priority.lower()]
    return {
        "total": len(issues),
        "issues": [issue.to_json() for issue in issues[:limit]],
    }


# ------------------------------------------------------------ entregables


@mcp.tool()
@_guard
def navis_discover(sample: int = 30000) -> dict[str, Any]:
    """Lee cómo está estructurado ESTE modelo, sin asumir nada.

    Muestrea el modelo y reporta cada propiedad que existe con su cobertura
    real, detecta sistemas de clasificación por la FORMA de los valores
    (OmniClass, Uniclass, MasterFormat, UniFormat, IFC, códigos corporativos) —no por
    el nombre del parámetro, que cambia según quién armó el modelo— y evalúa
    qué propiedad separa de verdad las especialidades.

    La prueba clave: una llave de disciplina agrupa por QUÉ SON las cosas; una
    de nivel agrupa por DÓNDE ESTÁN. Se mide con información mutua contra la
    categoría, así que funciona en cualquier idioma.

    Después infiere el rol de cada grupo por su CONTENIDO, no por su nombre:
    un grupo lleno de ductos es ventilación se llame como se llame.
    """
    from .discovery import discover, infer_group_roles

    # Any grouping already in memory belongs to whatever was open last. If
    # this call finds nothing confident, that stale key must not survive.
    STATE.clear_grouping()
    schema = STATE.bridge.model_schema(sample=sample)
    report = discover(schema)
    payload = report.to_json()
    payload["text"] = report.summary()

    if report.recommended:
        roles = infer_group_roles(report.recommended, STATE.profile)
        payload["proposed_roles"] = [r.to_json() for r in roles]
        STATE.discovery = report
        # Applied whenever the key is a real property, whatever kind it was
        # classified as. Keying on kind == "property" discarded the best
        # answer this tool produces: a "Source File" property gets
        # reclassified to kind "source_file" by its value shape, so the
        # recommendation was made, reported, and then silently ignored.
        # The gate is per group, not all-or-nothing. An earlier version
        # discarded the whole grouping when confident roles covered less than
        # 70% of elements — which on a real five-file federation threw away
        # four good roles because the fifth file scored 54%. The tagger
        # already falls back to the element category PER ELEMENT whenever its
        # group has no role, so partial roles are strictly better than none:
        # apply every confident one, let the rest resolve by category, and
        # say exactly that.
        confident = [r for r in roles if r.confidence >= 0.6 and r.role != "OTRO"]
        covered = sum(r.items for r in confident)
        total = sum(r.items for r in roles) or 1

        if not report.recommended.synthetic and confident:
            STATE.group_key = report.recommended.name
            STATE.group_roles = {r.value: r.role for r in confident}
        else:
            # Cleared, not left alone. A grouping key inherited from a
            # previous model is worse than none: the tagger silently keys on
            # a property this model does not publish, every group comes back
            # empty, and the run reports a clean project.
            STATE.clear_grouping()
            STATE.discovery = report

        uncovered = [r.value for r in roles if r not in confident]
        payload["applied"] = {
            "group_key": STATE.group_key or "(categoría del elemento)",
            "groups_with_role": len(STATE.group_roles),
            "elements_covered": f"{covered / total:.0%}",
            "why": (
                (
                    f"{len(confident)} grupos con rol confirmado por su contenido"
                    + (
                        f"; {', '.join(uncovered[:3])} se resuelven por categoría elemento a elemento."
                        if uncovered
                        else "."
                    )
                )
                if STATE.group_roles
                else "Ningún grupo tiene rol confiable; todo se resuelve por la categoría del elemento."
            ),
        }
    return payload


@mcp.tool()
@_guard
def navis_narrative_report() -> dict[str, Any]:
    """El informe redactado: qué le pasa a este modelo, en prosa.

    Separa lo que es problema de DISEÑO (decisiones que no se tomaron), de
    MODELADO (el modelo no representa el alcance real) y de COORDINACIÓN (dos
    especialidades que no han hablado) — que es la distinción que una lista de
    cruces nunca hace y de la que depende a quién se le pide qué.

    Emite además un veredicto de si el modelo está en condiciones de
    coordinarse punto por punto.
    """
    from .matrix import build_matrix
    from .narrative import compose

    result = STATE.require_fresh_result()
    matrix = build_matrix(result, STATE.profile)
    narrative = compose(
        result,
        matrix,
        STATE.profile,
        document_title=STATE.export.document_title if STATE.export else "",
        discovery=STATE.discovery,
        grouping_basis=STATE.group_key,
    )
    return narrative.to_json()


@mcp.tool()
@_guard
def navis_work_plan(limit: int = 15, session_max: int = 25) -> dict[str, Any]:
    """El plan de trabajo: las decisiones agrupadas en paquetes ejecutables.

    Mil decisiones ordenadas no son un plan. Nadie trabaja una lista de 1.375
    ítems, y entregarla es como termina un informe de coordinación sin leer:
    correcto, completo e inútil.

    Esto devuelve paquetes. Primero los SISTÉMICOS —una decisión que cierra
    cientos, como aprobar el listado de pasos— porque al aterrizar encogen
    todo lo demás. Después SESIONES: una pareja de especialidades, una zona,
    dimensionada para que quepa en una reunión, con un responsable. El resto
    queda contado como cola, no listado, porque fingir que es accionable
    infla el plan de vuelta a mil filas.
    """
    from .plan import build_plan

    result = STATE.require_fresh_result()
    plan = build_plan(result, STATE.profile, session_max=session_max)
    payload = plan.to_json(limit=limit)
    payload["text"] = plan.text(STATE.profile, limit=limit)
    return payload


@mcp.tool()
@_guard
def navis_coordination_matrix() -> dict[str, Any]:
    """Matriz de coordinación: decisiones abiertas por cruce de disciplinas.

    Responde una pregunta distinta a la lista priorizada. La lista dice qué
    abrir primero; la matriz dice **qué dos equipos tienen que sentarse**, que
    es lo que decide a quién se convoca a la reunión.
    """
    from .matrix import build_matrix, render_text

    result = STATE.require_fresh_result()
    matrix = build_matrix(result, STATE.profile)
    payload = matrix.to_json()
    payload["text"] = render_text(matrix, STATE.profile)
    return payload


def _image_options(overrides: dict[str, Any]) -> "ImageOptions":
    """Builds the option set, ignoring anything the caller left unset.

    Every visual argument defaults to `None` at the tool boundary rather than
    to its value, so "not mentioned" and "explicitly set to the default" stay
    distinguishable and the defaults can move without rewriting saved calls.
    """
    from .imaging import ImageOptions

    supplied = {key: value for key, value in overrides.items() if value is not None}
    return ImageOptions(**supplied).normalised()


def _capture_fetcher(options: "ImageOptions"):
    """Binds the bridge into the capture loop for one report run."""
    from .imaging import capture

    def fetch(guid: str, payload: dict[str, Any]) -> dict[str, Any]:
        return STATE.bridge.clash_image(guid, options=payload)

    def for_issue(issue) -> Any:
        return capture(
            fetch,
            issue.image_clash_id(),
            options,
            discipline_a=issue.discipline_a,
            discipline_b=issue.discipline_b,
            responsible=issue.responsible,
            caption=_caption(issue),
        )

    return for_issue


def _caption(issue) -> str:
    """What gets burned into the picture.

    Images leave reports: they are pasted into a chat, printed, pinned to a
    wall. At that point the page carrying the issue id and the level is gone,
    and an unlabelled render of a plenum is indistinguishable from any other
    render of any other plenum.
    """
    parts = [issue.issue_id]
    if issue.level:
        parts.append(issue.level)
    if issue.priority:
        parts.append(issue.priority.upper())
    return "  ·  ".join(p for p in parts if p)


@mcp.tool()
@_guard
def navis_pdf_report(
    path: str,
    max_issues: int = 25,
    with_images: bool = True,
    overwrite: bool = False,
    camera_mode: str | None = None,
    margin_percent: float | None = None,
    min_distance: float | None = None,
    max_distance: float | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
    background_color: str | None = None,
    hide_unrelated_geometry: bool | None = None,
    colorize_by_discipline: bool | None = None,
    show_clash_marker: bool | None = None,
    show_level: bool | None = None,
    show_grid: bool | None = None,
    render_quality: str | None = None,
    min_screen_fraction: float | None = None,
    min_pixel_fraction: float | None = None,
    max_attempts: int | None = None,
    keep_rejected: bool | None = None,
) -> dict[str, Any]:
    """Genera el informe PDF de coordinación.

    Portada con el resumen y las causas raíz, la matriz de coordinación con
    la agenda sugerida, y **una página por interferencia** con su imagen 3D
    renderizada por Navisworks, la ubicación, quién mueve, por qué importa y
    qué hacer.

    **Las imágenes se verifican, no se asumen.** De cada una se comprueba que
    los dos elementos en conflicto están dentro del encuadre (proyectando sus
    cajas por la cámara que se aplicó de verdad) y que ambos aparecen en los
    píxeles, que es lo único que detecta que un muro tapaba el cruce. La que no
    pasa se vuelve a tomar más abierta o aislando la geometría de por medio, y
    si aun así no sirve se descarta contándolo, en vez de publicar una foto que
    no muestra el problema. El resultado trae el recuento de pedidas,
    generadas, fallidas y descartadas, con el motivo de cada descarte.

    Todos los argumentos visuales son opcionales: sin ninguno, el informe sale
    igual que siempre pero con el encuadre nuevo.

    - `camera_mode`: `closeup` (por defecto), `context` o `plan`.
    - `margin_percent`: aire alrededor del cruce, 25 por defecto.
    - `min_distance` / `max_distance`: metros; acotan la cámara sin cambiar lo
      que entra en el cuadro.
    - `hide_unrelated_geometry`: aísla los dos elementos ocultando lo demás;
      se restaura al terminar cada captura.
    - `colorize_by_discipline`: un color estable por disciplina (por defecto).
      Con `False`, rojo es quien mueve y azul quien no se toca.
    - `render_quality`: `draft`, `standard` (por defecto) o `high`.

    Las imágenes se piden una por una y Navisworks tiene que posicionar cámara
    y renderizar cada vez, así que 25 problemas tardan algo. Usa
    with_images=False para un borrador rápido.
    """
    from .report import build_report

    result = STATE.require_fresh_result()

    options = _image_options({
        "camera_mode": camera_mode,
        "margin_percent": margin_percent,
        "min_distance": min_distance,
        "max_distance": max_distance,
        "image_width": image_width,
        "image_height": image_height,
        "background_color": background_color,
        "hide_unrelated_geometry": hide_unrelated_geometry,
        "colorize_by_discipline": colorize_by_discipline,
        "show_clash_marker": show_clash_marker,
        "show_level": show_level,
        "show_grid": show_grid,
        "render_quality": render_quality,
        "min_screen_fraction": min_screen_fraction,
        "min_pixel_fraction": min_pixel_fraction,
        "max_attempts": max_attempts,
        "keep_rejected": keep_rejected,
    })

    payload = build_report(
        path,
        result,
        STATE.profile,
        document_title=STATE.export.document_title if STATE.export else "",
        max_issues=max_issues,
        capture_fetcher=_capture_fetcher(options) if with_images else None,
        overwrite=overwrite,
    )
    payload["image_options"] = options.to_json()
    return payload


@mcp.tool()
@_guard
def navis_clash_image(
    issue_id: str = "",
    clash_guid: str = "",
    save_to: str = "",
    camera_mode: str | None = None,
    margin_percent: float | None = None,
    min_distance: float | None = None,
    max_distance: float | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
    background_color: str | None = None,
    hide_unrelated_geometry: bool | None = None,
    colorize_by_discipline: bool | None = None,
    show_clash_marker: bool | None = None,
    show_level: bool | None = None,
    show_grid: bool | None = None,
    render_quality: str | None = None,
    min_screen_fraction: float | None = None,
    min_pixel_fraction: float | None = None,
    max_attempts: int | None = None,
    keep_rejected: bool | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Renderiza UNA interferencia y dice si la imagen sirve.

    Para afinar el encuadre antes de gastar veinticinco renders en un PDF, y
    para comprobar una que salió mal. Devuelve las métricas completas —qué
    cámara se usó, qué fracción de la pantalla ocupa cada elemento, cuántos
    píxeles de cada color se contaron, qué intentos hubo y por qué— sin meter
    la imagen en la conversación.

    Con `save_to` escribe el PNG (dentro de las rutas autorizadas); sin él solo
    informa. La imagen nunca se devuelve en base64 al modelo: son cientos de
    kilobytes que no aportan nada a la decisión.

    Para inspeccionar por qué se descartó una imagen, `keep_rejected=True`
    guarda igualmente el mejor intento: mirar el encuadre rechazado es lo único
    que distingue «el muro tapaba el tubo» de «el control se pasó de estricto».
    """
    from .imaging import capture
    from .paths import policy as path_policy

    result = STATE.require_fresh_result()

    issue = None
    if issue_id:
        issue = STATE.issue(issue_id)
        guid = issue.image_clash_id()
    elif clash_guid:
        guid = clash_guid
    else:
        raise ValueError("Indica 'issue_id' o 'clash_guid'.")
    if not guid:
        raise ValueError(f"El problema {issue_id} no tiene ningún cruce que fotografiar.")

    options = _image_options({
        "camera_mode": camera_mode,
        "margin_percent": margin_percent,
        "min_distance": min_distance,
        "max_distance": max_distance,
        "image_width": image_width,
        "image_height": image_height,
        "background_color": background_color,
        "hide_unrelated_geometry": hide_unrelated_geometry,
        "colorize_by_discipline": colorize_by_discipline,
        "show_clash_marker": show_clash_marker,
        "show_level": show_level,
        "show_grid": show_grid,
        "render_quality": render_quality,
        "min_screen_fraction": min_screen_fraction,
        "min_pixel_fraction": min_pixel_fraction,
        "max_attempts": max_attempts,
        "keep_rejected": keep_rejected,
    })

    def fetch(target: str, payload: dict[str, Any]) -> dict[str, Any]:
        return STATE.bridge.clash_image(target, options=payload)

    shot = capture(
        fetch,
        guid,
        options,
        discipline_a=issue.discipline_a if issue else "",
        discipline_b=issue.discipline_b if issue else "",
        responsible=issue.responsible if issue else "",
        caption=_caption(issue) if issue else guid[:8],
    )

    payload = shot.to_json()
    payload["issue_id"] = issue.issue_id if issue else ""
    payload["document_title"] = STATE.export.document_title if STATE.export else ""

    if save_to and shot.png:
        authorised = path_policy().resolve_file(save_to, overwrite=overwrite)
        with authorised.staged_write() as staging:
            staging.write_bytes(shot.png)
        payload["path"] = str(authorised.path)
        payload["allowed_root"] = str(authorised.root)
    elif save_to:
        payload["path"] = ""
        payload["detail"] = "No se escribió nada: no hubo imagen que guardar."

    # `result` is read to enforce the freshness guard; naming it here keeps
    # that intent visible rather than looking like an unused local.
    payload["analysis_profile"] = result.profile_name
    return payload


# ------------------------------------------------------------ write-back


@mcp.tool()
@_guard
def navis_apply_groups(
    limit: int = 30,
    dry_run: bool = True,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Escribe los problemas calculados como grupos de clash en Navisworks.

    Es lo que hace que el análisis aterrice en el .nwf que el equipo abre, en
    vez de quedarse en un reporte aparte. Los nombres llevan prioridad y
    responsable para que la lista sea legible dentro de Clash Detective.

    Idempotente: el complemento reutiliza un grupo que ya exista con el mismo
    nombre en lugar de duplicarlo, y `idempotency_key` hace que un reintento
    devuelva el resultado original sin volver a tocar el documento.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    result = STATE.require_fresh_result()
    groups = []
    for issue in result.decisions[:limit]:
        pair = "×".join(d for d in issue.discipline_pair if d)
        groups.append(
            {
                "name": f"[{issue.priority.upper()}] {issue.issue_id} {pair} → {issue.responsible}",
                "clash_guids": issue.clash_ids,
            }
        )
    payload = STATE.bridge.apply_groups(
        groups, dry_run, fingerprint=fingerprint, idempotency_key=idempotency_key
    )
    payload.setdefault("document_fingerprint_before", fingerprint)
    return payload


@mcp.tool()
@_guard
def navis_set_status(
    issue_ids: list[str],
    status: str = "Reviewed",
    dry_run: bool = True,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Marca los cruces de esos problemas con un estado (New/Active/Reviewed/Approved/Resolved)."""
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    STATE.require_fresh_result()
    guids: list[str] = []
    for issue_id in issue_ids:
        guids.extend(STATE.issue(issue_id).clash_ids)
    payload = STATE.bridge.set_status(
        guids, status, dry_run, fingerprint=fingerprint, idempotency_key=idempotency_key
    )
    payload.setdefault("document_fingerprint_before", fingerprint)
    return payload


@mcp.tool()
@_guard
def navis_save_viewpoints(
    limit: int = 20,
    dry_run: bool = True,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Guarda un viewpoint por problema, para recorrerlos dentro de Navisworks."""
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    result = STATE.require_fresh_result()
    viewpoints = []
    for issue in result.decisions[:limit]:
        if not issue.clash_ids:
            continue
        viewpoints.append(
            {
                "clash_guid": issue.clash_ids[0],
                "name": f"{issue.issue_id} [{issue.priority}] {issue.responsible}",
            }
        )
    payload = STATE.bridge.save_viewpoints(
        viewpoints, dry_run, fingerprint=fingerprint, idempotency_key=idempotency_key
    )
    payload.setdefault("document_fingerprint_before", fingerprint)
    return payload


@mcp.tool()
@_guard
def navis_color_by_priority(
    limit: int = 50,
    dry_run: bool = True,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Colorea en el modelo los elementos de los problemas más graves.

    Rojo crítico, naranja alto, amarillo medio. Colorea el lado que DEBE
    MOVERSE, que es el que alguien tiene que encontrar.

    Qué lado es se resuelve por la disciplina del propio elemento, no
    comparando el responsable contra la pareja ordenada de disciplinas: esa
    inferencia acertaba aproximadamente la mitad de las veces y pintaba la
    estructura inamovible en rojo mientras el tubo que había que correr
    quedaba gris.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    result = STATE.require_fresh_result()
    palette = {"critical": (220, 30, 30), "high": (240, 130, 20), "medium": (230, 210, 40)}
    buckets: dict[str, list[str]] = {}
    unresolved: list[str] = []
    skipped: Counter[str] = Counter()

    for issue in result.decisions[:limit]:
        if issue.priority not in palette:
            skipped[issue.priority] += 1
            continue
        movable = issue.movable_elements()
        if not issue.elements_of(issue.responsible):
            # Both sides are the same trade, or an override retagged them:
            # everything involved gets coloured, and the issue is named so
            # nobody reads the colour as "this side must move".
            unresolved.append(issue.issue_id)
        buckets.setdefault(issue.priority, []).extend(movable)

    planned = []
    for priority, paths in buckets.items():
        unique = sorted(set(paths))
        if not unique:
            continue
        planned.append((priority, unique))

    if dry_run:
        return {
            "operation": "appearance/color", "status": "planned", "dry_run": True,
            "document_fingerprint_before": fingerprint,
            "requested": sum(len(paths) for _, paths in planned),
            "applied": 0, "verified": 0,
            "by_priority": {priority: len(paths) for priority, paths in planned},
        }

    applied = [
        {priority: STATE.bridge.color(
            paths, palette[priority], fingerprint=fingerprint,
            idempotency_key=f"{idempotency_key}:{priority}" if idempotency_key else "",
        )}
        for priority, paths in planned
    ]

    payload: dict[str, Any] = {
        "applied": applied,
        "document_fingerprint": fingerprint,
        "coloured": "el lado responsable de moverse",
    }
    if skipped:
        payload["skipped_by_priority"] = dict(skipped.most_common())
    if not applied:
        # An empty `applied` used to be the entire answer, which reads as a
        # failure when it is usually the opposite: nothing reached medium.
        # The operator stares at an uncoloured model with nothing to explain
        # it, so say which bucket everything fell into.
        detail = ", ".join(f"{n} {band}" for band, n in skipped.most_common())
        payload["note"] = (
            "No se coloreó nada: la paleta solo cubre crítico, alto y medio"
            + (f", y los problemas quedaron en {detail}." if detail else " y no hubo problemas.")
            + " Baja las bandas de prioridad en el perfil si querías verlos."
        )
    if unresolved:
        payload["ambiguous_issues"] = unresolved
        ambiguous = (
            f"En {len(unresolved)} problemas el responsable no se pudo localizar en un solo lado "
            "(misma disciplina en ambos, o retagueado por override): se colorearon TODOS sus "
            "elementos en vez de adivinar la mitad."
        )
        # Both conditions can hold at once; the second note must not erase the
        # first, or the caller loses half the explanation.
        prior = payload.get("note")
        payload["note"] = f"{prior} {ambiguous}" if prior else ambiguous
    return payload


@mcp.tool()
@_guard
def navis_reset_appearance(
    path_ids: list[str] | None = None,
    dry_run: bool = True,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Quita todos los colores aplicados sobre el modelo."""
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    if dry_run:
        return {
            "operation": "appearance/reset", "status": "planned", "dry_run": True,
            "document_fingerprint_before": fingerprint,
            "scope": "selección" if path_ids else "todo el modelo",
            "requested": len(set(path_ids or [])), "applied": 0, "verified": 0,
        }
    return STATE.bridge.reset_appearance(
        path_ids, fingerprint=fingerprint, idempotency_key=idempotency_key,
    )


# --------------------------------------------------------- flujo operativo


def _workflow(step: str, run_async: bool, payload: dict[str, Any]) -> dict[str, Any]:
    """Runs a ribbon step, synchronously or as a job.

    The same route either way: `job/submit` wraps it so the HTTP connection
    is not held open for the minutes a real federation takes, and the caller
    polls `navis_job_status` instead of watching a socket time out.
    """
    route = f"workflow/{step}"
    STATE.bridge.require(route)
    if run_async:
        return STATE.bridge.submit_job(route, payload)
    return STATE.bridge.call(route, payload)


@mcp.tool()
@_guard
def navis_audit_models(run_async: bool = False) -> dict[str, Any]:
    """Paso 0 — audita los modelos anexados ANTES de coordinar.

    Comprueba co-ubicación por caja envolvente (un modelo publicado sin las
    coordenadas compartidas correctas queda a kilómetros y TODOS sus tests dan
    cero: el falso verde más peligroso de la coordinación), la nomenclatura
    BEP, y la pureza de cada vista de coordinación — muros dentro del modelo
    eléctrico delatan una plantilla de vista sin filtrar en el Revit de origen.

    No modifica nada.
    """
    return _workflow("audit_models", run_async, {})


@mcp.tool()
@_guard
def navis_configure(
    run_async: bool = False,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Paso 1 — crea los search sets por disciplina y la matriz de clash.

    Lee el perfil corporativo y aplica exactamente lo mismo que el botón
    «1. Configurar» de la cinta: no hay dos implementaciones que puedan
    discrepar. Nunca destruye resultados ya corridos en silencio; un test con
    definición cambiada pero con resultados se conserva y se reporta como
    desactualizado, para que la decisión sea humana.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    return _workflow(
        "configure",
        run_async,
        {
            "expected_document_fingerprint": fingerprint,
            "idempotency_key": idempotency_key,
        },
    )


@mcp.tool()
@_guard
def navis_run(
    run_async: bool = True,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Paso 2 — corre todos los clash tests (equivale a Run All).

    Por defecto asíncrono: en un federado real esto retiene el hilo de UI de
    Navisworks durante minutos, y esperar en la conexión HTTP solo consigue
    que expire. Consulta el avance con navis_job_status.

    El progreso se reporta por FASE, no por porcentaje: TestsRunAllTests es
    una sola llamada atómica del API y cualquier barra sería inventada.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    return _workflow(
        "run",
        run_async,
        {
            "expected_document_fingerprint": fingerprint,
            "idempotency_key": idempotency_key,
        },
    )


@mcp.tool()
@_guard
def navis_group_levels(
    run_async: bool = True,
    expected_document_fingerprint: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Paso 3 — agrupa los resultados de cada test por nivel.

    Es la organización con la que se preparan las incidencias en ACC. Solo
    toca resultados sueltos: lo ya agrupado, por una corrida anterior o a
    mano, se respeta, así el paso es re-ejecutable tras cada Update All.

    Además renombra los choques con nombre por defecto (Clash 1, Conflicto 2)
    a «NNN · elemento vs elemento» y detecta interferencias-tipo repetidas en
    tres o más niveles, para abrir UNA incidencia por serie en vez de una por
    piso. Recuerda guardar: nada de esto queda en disco hasta hacerlo.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    return _workflow(
        "group_levels",
        run_async,
        {
            "expected_document_fingerprint": fingerprint,
            "idempotency_key": idempotency_key,
        },
    )


@mcp.tool()
@_guard
def navis_profile_info() -> dict[str, Any]:
    """Qué perfil usará de verdad el complemento en el próximo paso.

    Reporta esquema, checksum, nombre, cuándo se cargó, la sesión a la que
    pertenece y de dónde salió: `explicit_mcp` si lo enviaste con
    `navis_load_profile`, `plugin_default` o `packaged_default` si el
    complemento lo encontró en disco.

    Trae también el perfil del servidor, para que la pregunta «¿los dos lados
    están usando el mismo?» se responda con dos checksums en la misma
    respuesta en vez de con dos llamadas y una suposición.
    """
    info = STATE.bridge.profile_info()
    info["server"] = {
        "profile": STATE.profile.name,
        "checksum": STATE.profile_id,
    }
    info["in_sync"] = bool(info.get("checksum")) and info.get("checksum") == STATE.profile_id
    return info


# ----------------------------------------------------------------- trabajos


@mcp.tool()
@_guard
def navis_job_status(job_id: str) -> dict[str, Any]:
    """Estado, fase y avance de un trabajo en curso.

    Se responde sin tocar el documento, así que sigue contestando mientras
    Navisworks está ocupado con el trabajo. El avance es honesto: cuando el
    paso recorre unidades reporta unidades y porcentaje; cuando es una sola
    llamada atómica del API dice «indeterminate» y la fase, en vez de inventar
    una barra.
    """
    return STATE.bridge.job_status(job_id)


@mcp.tool()
@_guard
def navis_jobs() -> dict[str, Any]:
    """Los trabajos de esta sesión del puente, con el que esté activo."""
    return STATE.bridge.job_list()


@mcp.tool()
@_guard
def navis_cancel_job(job_id: str) -> dict[str, Any]:
    """Cancela un trabajo, cuando cancelarlo es realmente seguro.

    Uno que sigue en cola no ha tocado el documento: se descarta y ya. Uno en
    curso está dentro de una llamada del API en el hilo de UI y no existe
    interrupción soportada — abortar el hilo corrompe el documento en vez de
    detener el trabajo. Si el paso recorre unidades, se detiene en el próximo
    límite seguro y reporta «partial»; si es atómico, se te dice que no se
    puede y que terminará solo.
    """
    return STATE.bridge.job_cancel(job_id)


# ----------------------------------------------------------------- guardado


@mcp.tool()
@_guard
def navis_save(
    expected_document_fingerprint: str = "",
    run_async: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Guarda el documento abierto en su propia ruta.

    Todo lo que produce el flujo —sets, matriz, resultados, grupos, nombres—
    vive solo en memoria hasta que alguien guarda: cerrar Navisworks sin
    hacerlo descarta una corrida entera.

    Exige `expected_document_fingerprint` (léelo de navis_health): guardar es
    irreversible y no voy a escribir sobre un documento distinto del que
    creías. Si el archivo viene de ACC (.nwfacc) esto se rechaza con la razón
    — guardar encima NO publica nada en la nube y el siguiente sync de Desktop
    Connector puede reemplazarlo: usa navis_save_as a un .nwf local.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    payload = {"expected_document_fingerprint": fingerprint, "dry_run": dry_run}
    if run_async:
        return STATE.bridge.submit_job("document/save", payload)
    return STATE.bridge.save(fingerprint, dry_run=dry_run)


@mcp.tool()
@_guard
def navis_save_as(
    path: str,
    expected_document_fingerprint: str = "",
    overwrite: bool = False,
    run_async: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Guarda una copia del documento en otra ruta (.nwf o .nwd).

    Es la salida correcta para un archivo de ACC: un .nwfacc es la cara local
    de un documento cuyo maestro vive en la nube, no un .nwf con otro nombre.
    Guardar como .nwfacc se rechaza; guarda a un .nwf local y publica desde
    Desktop Connector.

    La ruta pasa por la política de salida del complemento (raíces
    autorizadas, sin UNC ni rutas de dispositivo) y `overwrite` es false por
    defecto. Después de escribir se verifica releyendo: que el archivo exista,
    que tenga bytes, que la marca de tiempo se haya movido, que el documento
    apunte al destino y que Navisworks ya no lo marque como modificado.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    payload = {
        "path": path,
        "expected_document_fingerprint": fingerprint,
        "overwrite": overwrite,
        "dry_run": dry_run,
    }
    if run_async:
        return STATE.bridge.submit_job("document/save_as", payload)
    return STATE.bridge.save_as(path, fingerprint, overwrite=overwrite, dry_run=dry_run)


@mcp.tool()
@_guard
def navis_close_document(
    disposition: str,
    expected_document_fingerprint: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Cierra el documento sin dejar una decisión de guardado a un diálogo.

    `disposition` es obligatorio: `save` guarda en la ruta actual antes de
    cerrar, `discard` descarta los cambios de forma explícita y
    `require_clean` se niega si queda algo sin guardar. Exige la huella del
    documento y por defecto solo muestra el plan (`dry_run=true`). Para un
    archivo sin destino local, usa primero `navis_save_as`.
    """
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    result = STATE.bridge.close_document(
        disposition,
        fingerprint,
        dry_run=dry_run,
    )
    if not dry_run and result.get("status") == "completed":
        STATE.forget_derived()
    return result


@mcp.tool()
@_guard
def navis_exit(
    disposition: str,
    expected_document_fingerprint: str,
    dry_run: bool = True,
    verify_timeout: float = 15.0,
) -> dict[str, Any]:
    """Cierra el documento y sale de Navisworks de forma verificable.

    Usa las mismas disposiciones explícitas de `navis_close_document`. El
    puente responde antes de solicitar WM_CLOSE y este servidor comprueba que
    el PID terminó. Si Navisworks sigue abierto —por ejemplo, por un diálogo
    de otro complemento— devuelve `partial`, nunca un falso `completed`.
    Con `dry_run=true` no cierra nada.
    """
    session = STATE.bridge.resolve(for_mutation=True)
    fingerprint = ""
    if session.document_open:
        fingerprint = STATE.require_mutable(expected_document_fingerprint)
    elif expected_document_fingerprint.strip():
        raise StateError(
            "La instancia elegida no tiene un documento abierto, pero se recibió una huella."
        )
    result = STATE.bridge.exit_application(
        disposition,
        fingerprint,
        dry_run=dry_run,
        verify_timeout=verify_timeout,
    )
    if not dry_run and result.get("application_exit_verified"):
        STATE.forget_derived()
    return result


@mcp.tool()
@_guard
def navis_select_issue(
    issue_id: str,
    dry_run: bool = True,
    expected_document_fingerprint: str = "",
) -> dict[str, Any]:
    """Selecciona en Navisworks los elementos de un problema."""
    fingerprint = STATE.require_mutable(expected_document_fingerprint)
    issue = STATE.issue(issue_id)
    path_ids = issue.elements_a + issue.elements_b
    if dry_run:
        return {
            "operation": "selection/set", "status": "planned", "dry_run": True,
            "document_fingerprint_before": fingerprint,
            "requested": len(set(path_ids)), "applied": 0, "verified": 0,
            "issue_id": issue.issue_id,
        }
    return STATE.bridge.select(path_ids, fingerprint=fingerprint)


# ------------------------------------------------------------- ecosystem


@mcp.tool()
@_guard
def navis_handoff(directory: str) -> dict[str, Any]:
    """Genera los artefactos que consumen las herramientas que consumen la coordinación.

    Escribe:
      · coordination_handoff.json — el contrato compartido, con los problemas
        resueltos hasta ElementId de Revit y código de presupuesto
      · revit_worklist.json — el trabajo agrupado por modelo de autoría, listo
        para que el MCP de Revit lo ejecute modelo por modelo
      · fct_issues.csv + brg_issue_elements.csv + dimensiones — esquema
        estrella para Power BI, unido por el mismo código de presupuesto que
        ya usa el tablero

    Reporta cuántos problemas son rastreables hasta Revit; los que no lo son
    se declaran en 'caveats' en vez de desaparecer.

    La carpeta se resuelve contra la política de salida (navis_output_policy).
    """
    result = STATE.require_fresh_result()
    if STATE.export is None:
        raise RuntimeError("No hay export en memoria. Corre navis_analyze primero.")
    return write_handoff(Path(directory), result, STATE.export, STATE.profile)


@mcp.tool()
@_guard
def navis_save_export(path: str, overwrite: bool = False) -> dict[str, Any]:
    """Guarda el export crudo en disco, para reanalizar sin abrir Navisworks.

    Útil para afinar el perfil: cambias un peso, corres `python -m naviscoord
    analyze` sobre el archivo y ves moverse el ranking.

    La ruta se resuelve contra la política de salida: solo se escribe bajo
    las raíces autorizadas (`navis_output_policy` las lista) y nunca se
    reemplaza un archivo existente sin overwrite=true.
    """
    if STATE.export is None:
        raise RuntimeError("No hay export en memoria. Corre navis_analyze primero.")
    authorised = output_policy().resolve_file(path, overwrite=overwrite)
    raw = STATE.bridge.export_clashes(
        properties=STATE.profile.section("interop").get("harvest_properties", [])
    )
    # Reserved atomically and published by rename: an export is tens of MB and
    # a run that dies halfway must not leave a truncated file that looks
    # analysable.
    authorised.write_text(json.dumps(raw, ensure_ascii=False))
    return {
        **authorised.to_json(),
        "clashes": len(raw.get("clashes", [])),
    }


@mcp.tool()
@_guard
def navis_output_policy() -> dict[str, Any]:
    """Dónde puede escribir este servidor, y cómo autorizar otra carpeta.

    Toda salida —PDF, handoff, CSV, export, save_as— se resuelve contra esta
    política. Una ruta fuera de las raíces permitidas se rechaza con el
    motivo, en vez de escribirse en cualquier sitio que pida quien llama.
    """
    return output_policy().describe()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
