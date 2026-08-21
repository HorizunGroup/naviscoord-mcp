# NavisCoord

<!-- mcp-name: io.github.horizungroup/naviscoord -->

Local MCP tools for Autodesk Navisworks coordination.

NavisCoord combines a Navisworks add-in with a Python MCP server. It helps an
AI client inspect a federated model, run and organize clash tests, reduce
duplicate clashes, identify root causes, prepare a coordination plan, and
generate reports without sending model data to a hosted service.

> NavisCoord is not affiliated with Autodesk. A licensed installation of
> Autodesk Navisworks Manage is required to use the add-in.

## What it does

- Discovers how models, properties, levels, and disciplines are organized.
- Builds search sets and clash matrices from configurable rules.
- Runs clash tests and groups results by level.
- Filters expected contacts and collapses repeated clashes into actionable
  issues.
- Highlights root causes, hotspots, priorities, and coordination decisions.
- Produces work plans, written reports, PDFs, CSV files, and handoff data.
- Supports multiple Navisworks instances with explicit targeting.
- Runs long operations as jobs with progress and cancellation reporting.
- Saves or saves-as only when explicitly requested and verifies mutations by
  reading the document again.
- Closes a document or exits Navisworks only after an explicit save, discard,
  or require-clean decision.

The analysis engine can also process a previously exported JSON file without
opening Navisworks.

## Requirements

- Windows.
- Python 3.10 or newer.
- Autodesk Navisworks Manage 2024, 2025, or 2026.
- .NET SDK 8 or newer only when building the add-in from source.

NavisCoord does not include Python, Navisworks, a Navisworks licence, or any
Autodesk assemblies.

## Compatibility

Autodesk Navisworks Manage 2024, 2025, and 2026.

## Install

NavisCoord has two components: the Navisworks add-in and the MCP server.

### 1. Install the Navisworks add-in

Close Navisworks first. From the
[latest release](https://github.com/HorizunGroup/naviscoord-mcp/releases),
download the ZIP for your Navisworks version and verify it with
`SHA256SUMS.txt`.

Extract it to:

```text
%APPDATA%\Autodesk\Navisworks Manage <version>\Plugins\NavisCoord\
```

Alternatively, clone the repository and build against every compatible
Navisworks installation found on the machine:

```powershell
.\install.ps1
```

Use `-Version 2025` to target one version or `-Uninstall` to remove the
add-in. Building from source requires the .NET SDK and a local Navisworks
installation.

### 2. Register the MCP server

The plugin launcher creates an isolated Python environment under
`%LOCALAPPDATA%\NavisCoord\runtime`. It uses an existing Python 3.10+
interpreter and installs its own dependencies there; it does not modify the
user's Python environment.

For Claude Code:

```text
/plugin marketplace add HorizunGroup/naviscoord-mcp
/plugin install naviscoord-mcp@horizun-navis
```

For Codex, add this repository as a marketplace and enable the
`naviscoord-mcp` plugin.

For a manual MCP configuration, install the Python package and adapt
[`.mcp.json.example`](.mcp.json.example):

```powershell
cd server
python -m pip install -e .
```

See [Installation](docs/INSTALL.md) for configuration variables and
troubleshooting.

### 3. Verify the connection

Open Navisworks Manage and a coordination document, then call:

```text
navis_health
navis_capabilities
navis_sessions
```

If several Navisworks instances are open, select one with `navis_target`
before making changes.

## First workflow

Start by inspecting the model:

```text
navis_health
→ navis_discover
→ navis_analyze
→ navis_root_causes
→ navis_work_plan
→ navis_pdf_report
```

To execute the operational coordination sequence exposed by the add-in:

```text
navis_audit_models
→ navis_configure
→ navis_run
→ navis_group_levels
→ navis_save_as
```

Ask for `navis_capabilities` before relying on a write operation. It reports
the routes supported by the installed add-in and its contract version.

## Interference images

`navis_pdf_report` puts one page per interference, each with a 3D image
rendered by Navisworks. Those images are checked before they are published:
the add-in projects both clashing elements through the camera it actually
applied, and the server counts the pixels each one painted — the only check
that can see a wall standing in front of the clash. An image that fails is
re-shot wider or with the intervening geometry hidden, and discarded with a
stated reason if it still does not show the problem.

Use `navis_clash_image` to tune the framing on a single issue before spending
twenty-five renders on a report. Both tools accept the same visual options
(`camera_mode`, `margin_percent`, `hide_unrelated_geometry`,
`colorize_by_discipline`, `render_quality`, …), all optional.

See [Interference images](docs/IMAGENES.md) for the option schema, the quality
metrics returned per image, and the Navisworks API limits behind two of the
options.

Useful inspection tools include:

- `navis_clash_image` for a single framed interference with its quality metrics.
- `navis_list_search_sets`, `navis_list_tests`, and `navis_coordination_matrix`.
- `navis_issue_detail`, `navis_hotspots`, and `navis_decisions`.
- `navis_jobs`, `navis_job_status`, and `navis_cancel_job`.
- `navis_output_policy`, `navis_current_target`, and `navis_profile_info`.

No save occurs unless `navis_save` or `navis_save_as` is called explicitly.
Use `navis_close_document` or `navis_exit` to end an unattended session; both
default to a dry run and require an explicit disposition for unsaved changes.

## Profiles

Project rules live in a profile: disciplines, tolerances, severity weights,
criticality, movability, search rules, and grouping criteria. Load one with
`navis_load_profile` or validate it from the command line:

```powershell
python -m naviscoord check-profile path\to\profile.json
```

The active profile is synchronized with the add-in so both sides use the
same configuration. See [Profiles](docs/PROFILES.md).

## Offline analysis

The Python engine can analyze a saved export without Navisworks:

```powershell
python -m naviscoord analyze export.json --profile project-profile.json
```

This mode supports analysis and reporting but cannot read or modify a live
Navisworks document.

## Safety

- The bridge listens on localhost and requires a per-session bearer token.
- Session files and the managed Python runtime are protected with restricted
  filesystem permissions.
- Output files are confined to configured roots; UNC paths and traversal are
  rejected.
- Writes require an explicit target when multiple sessions are available.
- Document fingerprints prevent a mutation from reaching an unexpected file.
- Mutations report success only after their result is read back.
- CSV and PDF output neutralize untrusted model text.

Read [Security model](docs/SECURITY-MODEL.md) for boundaries and threat
assumptions. Report vulnerabilities according to [SECURITY.md](SECURITY.md).

## Privacy Policy

NavisCoord runs on your machine. HorizunGroup operates no service for it and
receives no data from it: no account, no licence check, no telemetry, no
upload of models, clashes, or reports.

Two flows do leave the process, and both are yours to control:

- **Your AI client.** NavisCoord is an MCP server, so the summaries its tools
  return — issue descriptions, element ids, model file names, coordinates,
  report text — go to the MCP client you run it from, and through it to that
  client's model provider under their privacy policy. The raw clash export is
  never returned; large results are written to a file and answered with a
  path.
- **Your package index.** Launched as a plugin, NavisCoord provisions a
  plugin-local virtual environment and downloads its locked dependencies from
  PyPI. No model data is involved, and installing the server yourself skips
  it.

Files it writes — reports, handoffs, exports, session files — stay on your
disk, unencrypted, until you delete them.

The full policy, including what counts as personal data in a model and how
long anything is kept, is in [Privacy policy](docs/PRIVACY.md).

## Development

Run the Python test suite:

```powershell
cd server
python -m pytest tests -q
```

Run add-in logic tests without a Navisworks licence:

```powershell
dotnet run --project addin\NavisCoord.Tests
```

Build the release artifacts:

```powershell
python scripts\build_artifacts.py
```

## Documentation

- [Installation](docs/INSTALL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Interference images](docs/IMAGENES.md)
- [Sessions and targeting](docs/SESSIONS.md)
- [Long-running jobs](docs/JOBS.md)
- [Saving documents](docs/SAVING.md)
- [Profiles](docs/PROFILES.md)
- [Security model](docs/SECURITY-MODEL.md)
- [Privacy policy](docs/PRIVACY.md)
- [Testing](docs/TESTING.md)
- [Release process](docs/RELEASING.md)
- [Directory publishing](docs/PUBLISHING.md)

See [CONTRIBUTING.md](CONTRIBUTING.md) to contribute and
[SUPPORT.md](SUPPORT.md) for help.

## License

MIT. See [LICENSE](LICENSE). Third-party and Autodesk trademark notices are
documented in [NOTICE](NOTICE). No Autodesk assemblies are redistributed.
