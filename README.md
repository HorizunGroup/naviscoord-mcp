# NavisCoord

Coordination intelligence for Autodesk Navisworks, exposed over MCP.

It is not a remote control for Navisworks. It turns a clash detection run
into a short list of decisions — ranked, explained, with the root causes
named and the work grouped into packages a team can actually schedule.

4D and 5D are out of scope on purpose. This does one thing: coordination.

> ⚠️ Not affiliated with Autodesk. Requires a licensed installation of
> Autodesk Navisworks Manage to build and run the add-in.

## The problem

A federated model produces anywhere from 5.000 to 50.000 clashes. Those are
not 50.000 problems — they are perhaps 200, and perhaps 20 decisions. Every
published Navisworks MCP hands back the raw list and leaves you there, which
is exactly where coordination stalls today.

The design line follows from that: **the server does the deterministic heavy
lifting, the language model reasons over a compact summary.** If the model
has to read clashes one at a time, the approach has already failed.

## Status

| Component | State |
|---|---|
| Analysis engine (`server/naviscoord/`) | 61 tests, on both `mcp` 1.x and 2.x |
| Navisworks 2024–2026 add-in (`addin/`) | Compiled per version; both bridges verified live, including running side by side |
| MCP server, 27 tools | All exercised end to end through the MCP interface |
| Model structure discovery | Verified on four real coordination models |
| Plan, matrix, written report, PDF | Working |
| Add-in tests (`addin/NavisCoord.Tests`) | 59 checks over the JSON codec |

### Verified per version

| | Navisworks 2024 (API v21) | Navisworks 2026 (API v23) | Navisworks 2025 (API v22) |
|---|---|---|---|
| Bridge starts, health | ✅ live | ✅ live | builds clean against the 2025 API; no live run |
| Full flow over MCP (discover → analyze → plan → report → PDF) | ✅ live, real 3-model federation | ✅ live, two real projects | — |
| Clash test run (write path, native-handle lifetime) | ✅ live | ✅ live | — |
| Clash images in the PDF | ✅ 10/10 | ✅ live | — |
| Two versions open at once | ✅ 2024+2026 side by side, ports 8781/8782 | | |

The 2024 run was not a formality: it was done on a localized (Spanish)
install against a real three-model federation, and it surfaced three defects
the 2026 runs never showed — an all-or-nothing gate on the discovered
grouping, a report that named a grouping the analysis had not applied, and
uncontracted Spanish prepositions in assembled phrases. All three are fixed
and re-verified in the same session.

The engine runs **without Navisworks**: it takes a JSON export and returns
the analysis. That is what makes it testable, and what keeps the expensive
work off the UI thread.

## Install as a plugin (Claude Code / Codex)

Requires **Python 3.10 or newer** already on the machine. On first launch the
plugin provisions its dependencies into a plugin-local venv — never into your
interpreter — so there is no `pip` step. It does **not** ship or install a
Python: it builds that venv from whatever interpreter the client started it
with, and if that one is too old it says so and stops.

**Claude Code**

```
/plugin marketplace add HorizunGroup/naviscoord-mcp
/plugin install naviscoord-mcp@horizun-navis
```

**Codex** — add the marketplace from the repo URL and enable
`naviscoord-mcp@horizun-navis`.

Then install the Navisworks add-in, which needs Navisworks **closed** and so
cannot be automated from inside a running client:

```powershell
.\install.ps1
```

> Prebuilt add-in packages per Navisworks version are produced by
> `scripts/Build-Release.ps1`, but **no GitHub release has been published yet**. Until one
> is, `install.ps1` is the supported path — it needs the .NET SDK and a local
> Navisworks installation.

The bundled `naviscoord-setup` skill diagnoses and repairs both pieces.
Full detail in [docs/INSTALL.md](docs/INSTALL.md).

## Install from source

**1. Build and install the add-in** (Navisworks closed):

```powershell
.\install.ps1
```

Detects every installed Navisworks Manage 2024–2026, compiles the add-in
against each one's API and installs it per version — each release ships its
own API assembly, so one DLL cannot serve them all. `-Version 2025` limits to
one release; `-Uninstall` removes all. The script verifies what it copied
rather than assuming the copy worked.

Two versions open at the same time coexist properly: each bridge takes its
own port and writes **its own** session file under
`%LOCALAPPDATA%\NavisCoord\sessions\`. Pick one with `navis_target`; a
mutation with several open and none chosen is refused rather than guessed.
See [docs/SESSIONS.md](docs/SESSIONS.md).

**2. Install the Python package:**

```bash
cd server && pip install -e .
```

**3. Register the MCP server:** copy `.mcp.json.example` to `.mcp.json` and
adjust the paths.

Open Navisworks — the bridge starts by itself. If it does not, use the
`NavisCoord` button on the Add-Ins tab.

## Use

Analysis:

```
navis_health → navis_discover → navis_analyze → navis_work_plan
```

The four operational steps, the same services the ribbon buttons call:

```
navis_audit_models → navis_configure → navis_run → navis_group_levels → navis_save_as
```

Ask what the installed add-in can do before relying on it:

```
navis_capabilities   → contract version, routes, save/save_as, jobs, profile schema
navis_sessions       → open Navisworks instances, and which is selected
navis_output_policy  → where this server is allowed to write
```

Offline, against a saved export:

```bash
python -m naviscoord analyze export.json --profile my-project.json
```

Tests:

```bash
cd server && python -m pytest tests -q       # engine, security, targeting, packaging
dotnet run --project addin/NavisCoord.Tests  # add-in logic, no licence needed
python scripts/build_artifacts.py            # wheel + sdist, verified by reading them
claude plugin validate .                     # manifests
```

## How it thinks

```
tag → noise filter → collapse → congestion → severity → root cause → rank → fold
```

The order is not arbitrary. Tagging first because everything downstream keys
off discipline. Filtering before collapsing so noise cannot inflate a cluster
and fake a systemic problem. Congestion measured over collapsed issues rather
than raw clashes, so one messy crossing does not read as a crowded zone.

**Structure discovery.** Nothing is hardcoded to a standard. The tool samples
the model and reports every property with its real coverage, detects
classification systems by the **shape of the values** — OmniClass, Uniclass,
MasterFormat, UniFormat, IFC, in-house schemes — rather than by property
name, which changes with whoever set the model up.

The key test: a discipline key groups by **what things are**; a storey key
groups by **where they are**. Measured as mutual information against the
element category, so it works in any language. Then each group's role is
inferred from its **contents, not its name** — a group full of ducts is
ventilation whatever it is called.

**Noise filter.** Discards what nobody will action: a pipe against its own
elbow, insulation grazing a wall, a sleeve doing its job, a receptacle
embedded in the wall it is mounted on, a door inside its own opening,
anything already approved. Nothing disappears silently — every rejection is
counted by reason and can be audited.

**Collapse.** Two passes. First by pair identity, which absorbs multi-layer
hosts and reinforcement. Then a DBSCAN over the clash points, restricted to a
single discipline pair, which absorbs a run crossing many parallel elements.
The DBSCAN runs over a uniform grid rather than pulling in scipy: the radius
is fixed, so each neighbourhood query scans 27 cells and the engine stays
dependency-free.

**Storey reconciliation.** Federations rarely agree on level names. Matching
the strings would need a prefix list per office and ordinals per language,
and would still fail on `CUB` versus `ROOF`. The geometry does not need any
of that: two labels whose elements occupy the same band of elevation are the
same floor. On a real project this collapsed 21 labels into 9 storeys.

**Severity.** A weighted sum of six bounded components, each with a sentence
attached: penetration relative to the size of the element being bitten,
discipline-pair criticality, immovability (the real cost of the fix), cluster
size, congestion, and clearance violations. Arithmetic on purpose rather than
learned: a coordinator has to be able to argue with a ranking and change it.

**Root causes.** What actually compresses a coordination cycle:

- *systemic elevation* — a run buried the same amount into everything it
  crosses is at the wrong height, and one decision closes all N crossings
- *undefined penetrations* — services through walls and slabs with no sleeve
- *repeated typology* — the same crossing floor by floor is fixed in the type
- *congested zone* — where every move pushes the neighbour

**Work plan.** A thousand ranked decisions is not a plan. Nobody works a list
of 1.375 items. The output is packages: systemic ones first (one decision
that closes hundreds), then sessions sized to a single meeting — one
discipline pair, one zone, one owner. The rest is counted as tail, not
listed, because pretending it is actionable inflates the plan back to a
thousand rows.

## The profile decides

The engine ships no opinion of its own about what a serious clash is.
Weights, tolerances, the criticality matrix, movability and discipline rules
live in `server/naviscoord/profiles/default.json`, and ship with the package.
Two projects can disagree about severity without forking the code.

```bash
python -m naviscoord check-profile my-project.json
```

Three judgements the engine makes on its own, written down so they can be
argued with:

**Who moves.** Priority of way: whoever is hardest to reroute wins.
Structure never moves. Architecture defines the envelope and yields only to
structure — a wall is not shifted so a pipe can pass; the pipe reroutes or a
formal penetration is opened. Gravity drainage carries a fixed slope. From
there flexibility grows: duct, pipe, sprinkler, and finally electrical
conduit. **The same order on every project, deliberately** — if each job
negotiates it, two reports stop being comparable and priority becomes
opinion.

**What CRITICAL means.** One thing: it stops work on site or is expensive to
redo. Hence a narrow band — around 4-5% of a project. A band that takes half
the model does not prioritise, it renames the pile.

**How it speaks.** The report has to explain itself without translation. Not
"immovability component 0.8" but *"the hard side is structure — it does not
get touched on site: either the pipe reroutes, or you request a formal
penetration from the engineer before the pour"*. And elements are named the
way they are on site: a geometry node in a Revit export is named after its
material, so without this layer the text reads *"Copper enters 69 mm into
Concrete, Cast-in-Place gray"* instead of *"the copper pipe enters 69 mm into
the slab"*.

## Part of a tool ecosystem

Designed to sit alongside MCP servers for the authoring tools, sharing join
keys. `navis_handoff` writes:

| Artifact | Consumed by | For |
|---|---|---|
| `coordination_handoff.json` | shared contract | issues resolved down to authoring-tool element ids and budget codes |
| `revit_worklist.json` | the Revit MCP | work grouped by authoring model, only the side that must move, folded issues excluded |
| `fct_issues.csv` + `brg_issue_elements.csv` + dimensions | Power BI | star schema joined on the same budget code the dashboard already uses |

Without an element id a coordination issue is a dead end: nobody can go back
to the authoring model and fix it. So extraction harvests that property along
with any corporate parameter families, and the handoff **declares** how many
issues are not traceable rather than letting them disappear.

## Architecture decisions

- **Add-in targets .NET Framework 4.8, x64.** Verified against the shipped
  binaries: `Autodesk.Navisworks.Api.dll` is v21 (2024) through v23 (2026),
  all with TFM `.NETFramework,Version=v4.8`. Projects advertising .NET 8 mean
  their server, not their add-in.
- **One build per Navisworks version, one source.** The code restricts itself
  to API members present since v21; where a signature drifted between
  releases the call is resolved at runtime.
- **Port 8781**, not the 8765 every published bridge picked, so they coexist.
- **Session token in a header.** The published Navisworks MCP servers leave
  an unauthenticated HTTP listener on localhost, drivable by any process on
  the machine.
- **The Navisworks API is single-threaded**, so the listener runs on its own
  thread and marshals onto a UI dispatcher with a bounded FIFO queue.
- **No modal dialogs, ever.** A message box on the UI thread blocks it, and
  since every request marshals onto that thread, one unnoticed popup behind
  the main window freezes the whole tool. Everything goes to a log file.
- **The client survives a Navisworks swap without a visible error.** Closing
  2024 and opening 2026 mints a new session token, often on the same port.
  Measured live: the first call after the switch used to die with a rejected
  token and only the second recovered. Now, on a dead endpoint or rejected
  token the client re-reads the session file and, only if it actually
  changed, retries once — verified in both directions (2024→2026 and back)
  against one long-lived MCP server process. If the file is unchanged the
  error stands, because retrying against the same dead endpoint would mask a
  real outage.
- **Nothing is reported as done without re-reading it from the model.**

## Lessons from real models

Every one of these was found by running against a real coordination model,
and every one failed **silently** — the code did not throw, it returned a
plausible wrong answer.

1. **`ClassDisplayName` is not the Revit category.** It returns the
   Navisworks node class: `Solid`, the filename of every linked DWG, labels
   like `Type` or `Family`. The published `Category` property wins, with the
   node class only as a filtered fallback.
2. **`ReferenceEquals` does not work on this API.** Navisworks hands out a
   fresh wrapper on every property access, so reference identity never holds:
   path ids came back empty for *every* element, both sides of every clash
   shared the blank identity, and the engine discarded 8.101 of 9.676
   crossings as "element against itself" — reporting a clean project.
3. **A parent only counts when it is a real composite.** Using any ancestor
   as a collapse key fuses everything hanging off one category node.
4. **`TestsRunTest` invalidates native handles.** Holding a `ClashTest` across
   the call and reading it after throws `Object has been Disposed (WeakRef)`.
5. **Classification codes masquerade as categories.** A value like
   `D5010: Elevator` was checked raw and returned cleaned, so the bare code
   leaked through and became the second most common "category" in a model.
6. **Single-linkage clustering percolates.** Flood-filling adjacent congested
   cells merged 3.510 of 3.512 problems into one "zone". The same failure
   reappeared in storey reconciliation, where five labels chained across two
   real floors. Both now compare against the seed, not the neighbour.
7. **Conduit is not pipe.** In a Spanish Revit, `Tubo` is electrical conduit
   and `Tubería` is plumbing. Mapping `Tubos` to plumbing sent 4.147 elements
   of an electrical model to the wrong trade and flipped the whole file's
   inferred role.
8. **The clash API drifts between releases.** `ClashResult.TestType` and
   `ClashTest.DefaultAssignee` only exist from v22 on, and
   `TestsEditResultStatus` takes two arguments in v21 but three in v23 — so
   code that compiles cleanly against one version fails against its
   neighbour. The add-in sticks to the v21 surface and dispatches the drifted
   signature by reflection, with the re-read verification as the net.
9. **A major version can delete your entry point.** `mcp` 2.0 removed
   `mcp.server.fastmcp` outright — renamed to `MCPServer` in
   `mcp.server.mcpserver` — so an unbounded `mcp>=1.0` made every fresh
   install fail to import while pinned development machines saw nothing.
   The server now imports whichever is present and CI runs the suite against
   both majors, because "works here" is not evidence about a clean install.
10. **A wrapper without `functools.wraps` breaks every MCP tool.** FastMCP
   builds each schema by inspecting the callable's signature, so a bare
   `(*args, **kwargs)` wrapper advertised a schema demanding two literal
   fields named `args` and `kwargs`. All 26 tools registered, listed and
   described themselves perfectly — and not one could be called. Listing is
   not calling.

And one that was not a code defect but a judgement error: the detector
reported, at 100% confidence, that a receptacle family and a recessed light
fixture were at the wrong elevation — hundreds of crossings with millimetric
consistency. The pattern was real; the reading was wrong. A receptacle is
*supposed* to be embedded 100 mm into its wall. Hence the `flush_mounted`
rule, bounded by device depth so that a panel half a metre into a wall is
still a problem.

## Layout

```
addin/
  NavisCoord.Addin/       .NET Framework 4.8 in-process add-in
    HttpBridge.cs           loopback listener, token, concurrency
    Router.cs               route table, document reads, clash export
    SessionStore.cs         per-PID session registry, explicit DACL
    JobManager.cs           job state machine, honest progress
    MutationContract.cs     uniform envelope, fingerprint, idempotency
    CoordinationWorkflow.cs the four steps, as services
    SaveHandlers.cs         save / save_as, verified by re-reading
    PathPolicy.cs           output confinement
    ProfileSchema.cs        profile validation
    RulePrecedence.cs       rule precedence, search operators
    CoordinationLogic.cs    levels, repeat series, view purity
  NavisCoord.Tests/       console test runner, no NuGet, no licence needed
server/
  naviscoord/
    model.py              data contract (everything in metres)
    profile.py            project profile
    discovery.py          reads how a model is organised
    sessions.py           instance registry and targeting
    state.py              state bound to document + profile
    paths.py              output policy
    safety.py             markup escaping, CSV neutralisation
    matrix.py             coordination matrix
    plan.py               work packages
    narrative.py          the written report
    report.py             PDF
    interop.py            handoff to other tools
    bridge.py             HTTP client, capability negotiation
    mcp_server.py         MCP tools
    samples.py            synthetic exports with planted defects
    profiles/             shipped project profiles
    analysis/             tagging · noise · levels · clustering · severity · root cause
  tests/
docs/                     architecture, security model, sessions, jobs,
                          saving, profiles, install, testing, releasing
```

Files in the add-in with **no Autodesk reference** are that way on purpose:
they are the parts that had bugs, so they are the parts a runner with no
Navisworks licence can compile and assert.

Tests run against scenarios with deliberately planted defects. The failure
mode that matters here is not a crash: it is a credible ranking that is
quietly wrong, so each test asserts the engine finds exactly what was planted
and invents nothing else.

## Documentation

| | |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | The three pieces and the rule that separates them |
| [Security model](docs/SECURITY-MODEL.md) | What is protected, from what, and what is not |
| [Sessions](docs/SESSIONS.md) | Multi-instance, targeting, document-scoped state |
| [Jobs](docs/JOBS.md) | Long operations and honest progress |
| [Saving](docs/SAVING.md) | save / save_as and the ACC `.nwfacc` case |
| [Profiles](docs/PROFILES.md) | The single schema |
| [Install](docs/INSTALL.md) | Both pieces, Python required, troubleshooting |
| [Testing](docs/TESTING.md) | What runs in CI and what needs Navisworks |
| [Releasing](docs/RELEASING.md) | Release procedure and pending manual steps |

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) ·
Security reports: [SECURITY.md](SECURITY.md) ·
Support: [SUPPORT.md](SUPPORT.md) ·
Changes: [CHANGELOG.md](CHANGELOG.md)

## License

MIT — see [LICENSE](LICENSE). Third-party and Autodesk trademark notices are
in [NOTICE](NOTICE); no Autodesk assemblies are redistributed by this project.
