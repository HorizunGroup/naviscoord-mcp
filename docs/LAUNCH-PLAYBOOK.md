# Launch playbook

This is the distribution plan for NavisCoord. It deliberately starts with
proof, not directory submissions or generic “AI for construction” posts.

## The launch asset

Record one sanitized 60–90 second screen capture with this exact story:

1. Open a federation containing enough clashes to look painful.
2. Show the raw Clash Detective count for two seconds.
3. Ask NavisCoord to analyze without modifying or saving the model.
4. Show the reduction from raw clashes to issues, root causes and packages.
5. Open one explained issue and one systemic cause.
6. Show the generated coordination plan and PDF.
7. End on the sentence: **“From clashes to coordination decisions.”**

Do not accelerate the video so much that viewers cannot verify the result. Do
not expose client names, file paths, tokens, user names or model geometry that
is not cleared for publication. Put the exact prompt, profile and sanitized
export used by the demo in the repository so the claim is reproducible.

## Message hierarchy

Always lead with the coordination outcome:

> NavisCoord turns thousands of Navisworks clashes into explainable, verified
> work packages for BIM/VDC coordination.

Then add proof:

- deterministic, profile-driven severity;
- root-cause and hotspot detection;
- explicit dry-run, document targeting and post-write re-reads;
- local-only bridge and auditable PDF/CSV outputs;
- Navisworks Manage 2024–2026.

Avoid leading with tool counts, “agents”, generic artificial intelligence or
the protocol acronym. Those describe implementation, not why a coordinator
should spend ten minutes trying the product.

## Four-week launch sequence

### Week 1 — evidence

- Publish the sanitized demo and its reproducibility bundle.
- Publish a GitHub release whose checksums and beginner installer are tested
  from a clean Windows user account.
- Open one pinned Discussion: “Show your coordination workflow”.
- Ask three BIM/VDC practitioners to follow the quick start without help;
  repair every point where two people hesitate.

### Week 2 — owned audiences

- Publish one technical article: why clash count is not decision count.
- Publish one practitioner article: how to prepare a coordination meeting.
- Cut the demo into a 15-second loop and a 45-second narrated version.
- Post from the maintainer and organization accounts with the same promise,
  demo and first prompt; do not write unrelated variants for every network.

### Week 3 — ecosystem distribution

- Publish the Python package and submit valid metadata to the official MCP
  Registry.
- Submit the project to `awesome-mcp-servers` after the public demo works.
- Contact AEC/BIM newsletters, Autodesk community authors and MCP directory
  maintainers with a reproducible example, not a press-release paragraph.
- Cross-link from Horizun's Revit MCP only where the workflows genuinely
  complement one another.

### Week 4 — proof loop

- Publish the first sanitized case study with raw clashes, actionable issues,
  root causes, packages, elapsed time and limitations.
- Convert recurring installation questions into documentation and tests.
- Create labels for `good first issue`, `integration`, `live-api` and
  `documentation` only when there are real issues ready for contributors.
- Announce improvements with before/after evidence instead of reposting the
  original launch.

## Copy blocks

### Short launch post

> NavisCoord is an open-source MCP server for Autodesk Navisworks Manage. It
> turns raw clash results into explainable root causes and coordination work
> packages, with guarded and verified model workflows. Install it, paste one
> read-only prompt and inspect the evidence: [repository link].

### Technical opening

> If an LLM has to inspect 20,000 clashes one at a time, the architecture has
> already failed. NavisCoord performs deterministic filtering, clustering,
> ranking and planning locally, then gives the model compact evidence it can
> explain.

### Call to action

> Try the read-only first prompt. If it saves a coordination session, star the
> repository and share sanitized before/after counts in Discussions.

## Metrics that matter

Track weekly, with a source link or release telemetry where available:

| Stage | Metric | First target |
|---|---|---:|
| Discovery | Qualified repository visitors | 1,000 |
| Interest | Demo completion rate | 35% |
| Intent | Release/installer downloads | 150 |
| Activation | Verified first analyses reported | 25 |
| Retention | Teams using it in a second week | 10 |
| Advocacy | Public sanitized examples | 3 |
| Community | External contributors with accepted PRs | 3 |

Stars are useful discovery evidence, but they do not substitute for activation
or repeat use. Never collect model contents, file paths or client identities to
improve these metrics.

## Release gate before promotion

Do not launch broadly until all are true:

- the demo can be reproduced from public files;
- the beginner path was tested by people who did not build the project;
- the latest release contains per-version ZIPs and `SHA256SUMS.txt`;
- the public default branch matches the version being promoted;
- security, support and contribution links resolve;
- every published performance or reduction number names its input and method;
- no screenshot, log or artifact contains client or workstation data.
