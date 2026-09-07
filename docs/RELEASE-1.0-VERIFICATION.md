# NavisCoord 1.0 release verification

Status: verification in progress; no 1.0 release has been published. Base: public main `2eddbc5`. Evidence below was collected on 2026-09-06/07. Local testing is distinct from marketplace approval.

| Area | Evidence | State |
|---|---|---|
| Engine and packaging | Full Python suite: 983 passed, 2 skipped, including final report regressions | Passed |
| Add-in shared logic | `dotnet run --project addin/NavisCoord.Tests -c Release`: 1,860 checks | Passed |
| Native assemblies | Build-Release: 2024, 2025, 2026; public-path guard passed | Passed |
| Runtime dependencies | Clean environment, 33 pinned packages installed with hashes; pip check | Passed |
| Real MCP protocol | Standalone executable and PowerShell plugin launcher: initialize, 53 tools, tool call, live health | Passed |
| Runtime repair | Verified download, corrupt-file repair and offline repeated installation | Passed |
| Add-in recovery | 24 backup/install/update/restore and failure-injection checks | Passed |
| Public artifact guard | 21 self-tests | Passed |
| Claude bundle | Official MCPB CLI validates and packs the binary bundle | Passed |
| Local desktop plugin | Personal marketplace registration and Codex plugin installation; local launcher reaches Navisworks | Passed |
| Work desktop conversation | User confirmed navis_health connects to add-in 1.0.0.0 from a new Work desktop conversation | Passed (user reported) |
| Claude Desktop extension | MSIX configuration written; real app restart and tool invocation pending | Pending |
| Claude Code | Standalone 1.0 registration, repeated update and Claude CLI Connected status | Passed |
| Public release and directories | Not yet published or submitted as 1.0 | Pending |

## Live acceptance

The 1.0.0.0 add-in in Navisworks Manage 2024, 2025 and 2026 ran Autodesk's gatehouse sample. Two search sets inside a folder were read back; a matrix referencing those nested sets was created; missing sets and invalid test types were refused before mutation. The clash test produced 208 results, grouped into 68 issues. Snapshot round-trip comparison succeeded. Group write-back was verified, and the resulting stale analysis was refused. The PDF embedded both requested images; source NWD was not saved.

This DWG-derived sample provides generic categories and CAD layers, so it tests integration rather than discipline-classification accuracy. Reviewing its rendered PDF exposed and led to corrections for false floor inference, movement instructions without discipline mapping, and missing report warnings. The final regression cases cover these conditions. Decision counts change when unsupported inferred root causes are removed; they are not a claim of engineering-approved closure.

## Audit corrections covered by regression cases

Relationship-aware noise filtering; scoped host identities across source models; all folded occurrences retained in group writes and Revit handoffs; revision checks on reads and derived writes; computed vertical separation alternatives; hosted opening containment; complete inventory metadata; bounded spatial clustering; stable IDs under ranking changes; real matrix coverage in snapshots; malformed snapshot refusal; enforced read-only routes; correct fingerprint/idempotency forwarding; nested search-set lookup; and repairable standalone installation.

The release requires a final full test run, CI on the exact commit, final artifact rebuild, client checks and recorded distribution outcomes. An external directory's pending review is not described as verification.

CI on cc248d0 passed all 20 jobs, including the required ci-ok, Python 3.10–3.14 with MCP 1.x/2.x, clean Python packaging and the standalone Windows runtime. Client installer follow-up changes require a subsequent run.

Final client-code CI on `e9add09` passed the required `ci-ok`. Live acceptance subsequently passed in 2024 and 2025 with 208 raw clashes and 68 issues in each. All QA document changes were discarded without saving; process exit was checked. The 2025 process exited after the bounded verification window, so the first exit response correctly reported incomplete verification, followed by confirmed absence of the process.
