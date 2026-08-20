# Product and GitHub benchmark

Snapshot: **2026-08-20**. Star and fork counts are volatile; they are context,
not a quality score. Repository metadata was read from the public GitHub API.

## Executive conclusion

NavisCoord should not position itself as a generic “AI remote control for
Navisworks”. That category is crowded, easy to copy, and makes tool count look
more important than coordination outcomes.

The defensible category is:

> **Coordination intelligence for Navisworks — from thousands of clashes to
> explainable, verified construction decisions.**

The code is already unusually strong in analysis, mutation safety and public
evidence. The adoption gap is distribution: visual proof, beginner onboarding,
package/registry reach and a repeatable launch loop.

## Public comparison

| Project | Stars | Forks | What it teaches | What NavisCoord should copy — or avoid |
|---|---:|---:|---|---|
| [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp) | 36,302 | 3,042 | A tiny getting-started path and an immediate first interaction | Copy the “install, then ask this” structure; keep advanced configuration below the fold |
| [GitHub MCP Server](https://github.com/github/github-mcp-server) | 32,383 | 4,827 | One-click installs, client-specific guides, explicit read-only/lockdown modes | Copy installation affordances and security modes; avoid making the README an option reference |
| [Blender MCP](https://github.com/ahujasid/blender-mcp) | 26,083 | 2,485 | Visual demonstrations and prompts that make the result imaginable | Produce a real Navisworks-to-report demo; do not rely on architecture prose as the hero |
| [AEC Model Bridge](https://github.com/Sam-AEC/aec-model-bridge) | 54 | 18 | Broad AEC interoperability is attractive | Stay narrower: owning coordination is more memorable than claiming every AEC workflow |
| [Horizun Revit MCP](https://github.com/HorizunGroup/horizun-revit-mcp) | 12 | 0 | Strong trust narrative, reproducible benchmark and AI-readable metadata | Share ecosystem links, benchmark format, `llms.txt` and release discipline |
| **NavisCoord before this review** | **12** | **0** | Deep engineering and real-model evidence | No GitHub topics, no visual hero, an overwhelming README and no official-registry/PyPI presence hid the value |

Other direct AEC/Navisworks projects observed in the same snapshot were still
small: [BimOnMcp](https://github.com/General-Soju/BimOnMcp) had 8 stars and
[ScanBIM MCP](https://github.com/ScanBIM-Labs/scanbim-mcp) had 5. The category
is not yet owned. That is an opportunity, not proof that demand exists.

## Weighted scorecard

The target is not a vanity “100”. A score is earned only when a stranger can
observe the evidence.

| Dimension | Weight | Before | With this repository work | What earns full credit |
|---|---:|---:|---:|---|
| Search relevance and metadata | 10 | 2 | 10 | Description and 20 focused GitHub topics |
| Five-second value proposition | 10 | 5 | 10 | Outcome, audience and product named above the fold |
| Visual proof | 10 | 1 | 4 | Architecture visual now; a real 60–90 s demo is still required |
| Beginner installation | 15 | 5 | 12 | Release installer and five-minute guide; signing/SmartScreen remains |
| First successful workflow | 10 | 5 | 10 | Copyable prompt, expected checks and recovery path |
| Expert technical evidence | 15 | 15 | 15 | Tests, threat model, deterministic artifacts and live-test boundaries |
| Safety and trust | 10 | 10 | 10 | Explicit targeting, dry runs, re-read verification and local-only model data |
| Community readiness | 5 | 3 | 5 | Discussions enabled; contribution, support, security and issue forms present |
| Distribution | 10 | 1 | 4 | GitHub/Claude/Codex exist; PyPI and the official MCP Registry remain |
| Retention and social proof | 5 | 1 | 2 | Releases exist; public case studies and a recurring demo cadence remain |
| **Total** | **100** | **48** | **82** | The remaining 18 points require public assets and channels, not more README text |

## Why the leading projects convert

1. **They show the outcome before explaining the implementation.** Blender
   MCP lets a visitor imagine a scene; Playwright gives the first prompt next
   to installation.
2. **The happy path is one command or one button.** Every extra prerequisite
   loses users before product value appears.
3. **They support the client a visitor already uses.** Client-specific snippets
   beat one abstract MCP explanation.
4. **They create trust at the point of risk.** GitHub MCP puts read-only and
   lockdown modes beside installation, not in a distant security paper.
5. **They are discoverable outside GitHub search.** Package registries, MCP
   registries, curated lists, videos and third-party tutorials all compound.

## Positioning system

### Category

**Navisworks coordination intelligence**

### One-line promise

**Turn thousands of Navisworks clashes into explainable, verified decisions
your coordination team can schedule.**

### Audience

- BIM/VDC coordinators who own clash review and coordination meetings.
- MEP, architecture and structure leads who need actionable work packages.
- Contractors and consultancies that need auditable reports and handoff data.
- MCP/AEC developers who need a safe reference architecture for an in-process
  Autodesk bridge.

### Proof pillars

1. **Compression:** raw clashes become issues, root causes and meeting-sized
   packages.
2. **Explainability:** deterministic severity and a configurable project
   profile, not an opaque learned score.
3. **Verified action:** a write is not complete until the document is read
   again.
4. **Local trust:** model data stays on the workstation; the bridge is
   loopback-only and session-authenticated.
5. **Real-model learning:** public tests and documented production failure
   modes, with unsupported live coverage named explicitly.

## Gaps that documentation cannot hide

These are the next distribution milestones, in order:

1. Record one public, sanitized 60–90 second video: raw Clash Detective result
   → root causes → work plan → PDF with images.
2. Publish a signed beginner installer or release bundle that avoids PowerShell
   execution-policy and SmartScreen friction.
3. Publish `naviscoord` on PyPI with
   `mcp-name: io.github.HorizunGroup/naviscoord-mcp`, then publish `server.json`
   to the [official MCP Registry](https://registry.modelcontextprotocol.io/).
4. Publish one reproducible, sanitized case study with before/after counts and
   the profile used.
5. Submit only after that evidence exists to the major MCP/AEC directories;
   directory spam before a credible first run wastes the launch.

The recording script, channel sequence, copy blocks and activation metrics are
in the [launch playbook](LAUNCH-PLAYBOOK.md).

## Benchmark method

- GitHub stars, forks, topics and repository descriptions came from the GitHub
  REST API on the snapshot date.
- Product and installation observations came from each repository's public
  README and official documentation.
- Scores are a disclosed product heuristic, not an external certification.
- No score gives credit for a claim that cannot be inspected in the public
  repository or a published artifact.
