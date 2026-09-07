# Distribution and directory publication

The source changes for 1.0 are public in [PR #19](https://github.com/HorizunGroup/naviscoord-mcp/pull/19). The stable 1.0 release is not published yet. Directory submission must reference available, tested artifacts; this document does not claim that any submission has been approved.

| Channel | Distribution route | Current 1.0 status |
|---|---|---|
| GitHub Releases | Stable tag, three Navisworks add-in ZIPs, runtime ZIP, desktop ZIP, MCPB, wheel/sdist, SHA256SUMS.txt | Built locally; merge and final release pending |
| ChatGPT Work desktop / Codex | Local Personal marketplace and standalone runtime | Installed locally; Codex CLI installation and MCP protocol passed; Work connection to add-in 1.0.0.0 confirmed by the user |
| Claude Code | Repository plugin or direct stdio registration | Direct 1.0 connection and repeated registration verified |
| Claude Desktop | Standalone MCPB or local stdio configuration | Official bundle validation passed; connection to add-in 1.0.0.0 confirmed by the user |
| Claude desktop extension directory | [Official submission instructions](https://claude.com/docs/connectors/building/submission) | Submitted 2026-09-07 UTC; form confirmed receipt; awaiting Anthropic evaluation |
| Claude plugin directory | [Plugin submission portal](https://platform.claude.com/plugins/submit) | Not submitted as 1.0 |
| PyPI | Versioned wheel and sdist, then authenticated upload or configured Trusted Publisher | Artifacts built; publication credentials/setup not established |
| Official MCP Registry | `server.json`, published Python package, GitHub namespace authentication | Metadata prepared; depends on PyPI publication |
| Glama | [Repository indexing and ownership verification](https://glama.ai/mcp/methodology) | No existing listing found; submission requires interactive GitHub sign-in, not completed |
| MCPFly | [Repository submission](https://mcpserver.so/submit) | Submitted 2026-09-07 UTC; ID `HorizunGroup/naviscoord-mcp`; pending approval |
| MCP.so | [Paid submission](https://mcp.so/submit?type=server) | Current form requires a $39 payment; no purchase made |
| Additional community directories | Assess current submission requirements and avoid duplicate listings | No other submission claimed |

## Submission receipts

**Anthropic desktop extensions — 2026-09-07 UTC.** After the publisher authorized the review contact and directory terms, the official MCPB form accepted `naviscoord-1.0.0-win-x64.mcpb` and displayed **“Your response has been recorded.”** The form stated that a response copy would be emailed to the authorized contact. The submission identifies the bundled Python runtime, the independent relationship to Autodesk, the public source PR, the privacy policy and the pending independent merge review. It does not claim that stable release publication or directory approval has occurred.

Submitted runtime source: `0a10f4fc5f0fd3398800f6dfd6d32d415ddeaddc`. MCPB SHA256: `bb99934a31490eb5d1751d8b45fbc6c109752036fcb68bf4de0d02978b58a6eb`. The subsequent desktop-installer change does not alter this MCPB, which contains the standalone runtime. The personal contact and editable-response link are intentionally excluded from public documentation.

**MCPFly — 2026-09-07 UTC.** The form accepted the public GitHub repository as an MCP server and displayed **“Submission received and pending approval”**, with submission ID `HorizunGroup/naviscoord-mcp`. This is a repository-listing request, not a claim that a 1.0 package is already publicly downloadable.

## Ready-to-use listing information

**Name:** NavisCoord by HorizunGroup

**Tagline:** Turn Navisworks clashes into coordination decisions.

**Description:** NavisCoord connects a desktop AI client to Autodesk Navisworks Manage on the same Windows computer. It exposes 53 MCP tools for model discovery, clash tests, evidence-aware analysis, coordination groups, revision comparisons and reports. Document writes check their target and report verification outcomes. The standalone runtime includes its dependencies. Live use requires licensed Navisworks Manage 2024, 2025 or 2026. Requested tool outputs can include model properties, coordinates and images sent to the user's AI client.

**Repository:** https://github.com/HorizunGroup/naviscoord-mcp

**Documentation:** https://github.com/HorizunGroup/naviscoord-mcp/blob/main/docs/INSTALL.md

**Privacy:** https://github.com/HorizunGroup/naviscoord-mcp/blob/main/docs/PRIVACY.md

**Support:** https://github.com/HorizunGroup/naviscoord-mcp/issues

**Icon:** `assets/icon.png`. Obtain the review contact from the publisher; do not invent an email address or publish a private account address without authorization.

## Publication requirements

The [official MCP Registry quickstart](https://modelcontextprotocol.io/registry/quickstart) requires the referenced package to be published first. `server.json` and the PyPI README must agree on `io.github.HorizunGroup/naviscoord-mcp`. Verify the wheel includes the README ownership marker and matches the declared release version before upload.

Claude's [official submission page](https://claude.com/docs/connectors/building/submission) distinguishes local desktop MCPB submissions from the remote connector portal. Local bundles have a separate form and require a public privacy policy and annotated tools. A successful form submission is pending review, not certification. Record the actual submission URL or receipt and status here when available.

For each directory, verify the repository is not already listed, submit only supported local-client capabilities, and retain a record of the outcome. Do not manufacture public endpoints, Windows signing trust, approval badges or zero-defect guarantees to meet a form's requirements.
