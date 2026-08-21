# Privacy Policy

**Applies to:** NavisCoord — the Navisworks add-in, the MCP server, and the
plugin that packages them.
**Last updated:** 2026-08-20 (NavisCoord 0.4.0).

## Summary

NavisCoord runs entirely on your computer. HorizunGroup operates no service
for it, receives no data from it, and has no way to see your models, your
clashes, or your reports. There is no account, no licence check, and no
telemetry of any kind.

Two things do leave the process, and both are described in full below: the
answers your AI client asked for, and — the first time the plugin provisions
its runtime — a package download from PyPI.

## What NavisCoord processes

Everything NavisCoord handles comes from the Navisworks document you already
have open, or from configuration you supply:

- **Model data.** Names and file paths of the attached models, element
  properties harvested according to the active profile, clash results and
  their coordinates, selection and search sets, level and discipline names.
- **Configuration.** The coordination profile: weights, tolerances,
  discipline rules, output roots.
- **Session metadata.** The Navisworks process id, the loopback port, a
  per-session bearer token, and a fingerprint of the open document.

Model data can incidentally contain personal data — a Windows user name
inside a file path, an author or designer name stored as a model property.
NavisCoord does not seek it out, and does not treat it differently from any
other property; it travels wherever that property travels.

## Where it goes

**In memory.** The clash export and the analysis result live in the MCP
server process for the length of the session and are discarded when it ends.

**Over loopback only.** The MCP server reaches the Navisworks add-in at
`http://127.0.0.1` on a port between 8781 and 8788, authenticated with a
per-session token. The add-in rejects any request that did not arrive on the
loopback interface. No model data is sent to any other host.

**To disk, where you asked for it.** PDF reports, handoff JSON and CSV,
rendered images and raw exports are written only under the output roots you
configured or the default export folder
(`%LOCALAPPDATA%\NavisCoord\exports`). Session files are written to
`%LOCALAPPDATA%\NavisCoord\sessions` with permissions restricted to your
account, SYSTEM and Administrators. All of these files are written
unencrypted: anyone who can read that folder can read them.

**To your AI client.** This is the flow worth understanding. NavisCoord is an
MCP server, so what its tools return goes to the MCP client you run it from
— and, through that client, to whichever model provider that client uses,
under that provider's own privacy policy. What is returned is a ranked
summary: issue descriptions, priorities, element ids, model file names,
coordinates, and the text of the reports. The raw clash export is deliberately
not returned; tools that would produce thousands of rows write a file and
return its path instead. NavisCoord has no control over, and no visibility
into, what your client does with an answer once it has it.

**To PyPI, once, when installed as a plugin.** Launched as a Claude Code or
Codex plugin, NavisCoord provisions a plugin-local virtual environment and
installs its locked dependencies (`mcp`, `reportlab`, `pillow`) with `pip`.
That is an ordinary package download over HTTPS from your configured package
index. It carries no model data and no information about you beyond what any
`pip install` sends. It happens when the runtime is missing or out of
contract, not on every start. Installing the server yourself with
`pip install -e server` skips it entirely.

## What NavisCoord does not do

- No telemetry, analytics, usage metrics, or crash reporting.
- No account, no sign-in, no licence server, no phone-home check.
- No upload of models, clashes, reports, or profiles anywhere.
- No reading of files beyond the document you have open, the profile you
  point it at, and the paths you pass to its tools.
- No advertising, no profiling, no sale or sharing of data — there is no
  data on our side to sell or share.

## Retention

HorizunGroup retains nothing, because it receives nothing.

On your machine, the files NavisCoord writes stay until you delete them; it
never removes a report or an export on its own. A session file belongs to one
Navisworks process and is not a history: NavisCoord keeps no log of past
sessions, past documents, or past analyses across restarts.

## Third parties

- **Autodesk Navisworks** — the host application. NavisCoord does not change
  what Autodesk itself collects; see Autodesk's privacy statement for that.
- **Your MCP client and its model provider** — as described above.
- **PyPI and the packages `mcp`, `reportlab`, `pillow`** — dependencies, used
  locally. NavisCoord sends no data through them.

## Children

NavisCoord is a professional engineering tool. It is not directed at children
and is not intended for use by anyone under 16.

## Changes to this policy

This policy is versioned in the repository alongside the code it describes.
Changes are published at
<https://github.com/HorizunGroup/naviscoord-mcp/blob/main/docs/PRIVACY.md>
with an updated date, and material changes are called out in
[CHANGELOG.md](../CHANGELOG.md).

## Contact

- Questions about this policy, or about what NavisCoord does with a
  particular piece of data: open an issue at
  <https://github.com/HorizunGroup/naviscoord-mcp/issues>.
- Suspected vulnerabilities: do not open a public issue. Follow
  [SECURITY.md](../SECURITY.md), which uses GitHub's private reporting.

For the boundaries this policy relies on — how the loopback bridge is
authenticated, how output paths are constrained, and what the design
explicitly does not defend against — see the
[security model](SECURITY-MODEL.md).
