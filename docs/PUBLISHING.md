# Distribution and directory publication

The stable [NavisCoord 1.0.0 release](https://github.com/HorizunGroup/naviscoord-mcp/releases/tag/v1.0.0) was published on 2026-09-07 at 03:56 UTC after independent approval and merge of PRs #19 and #20. Its original eleven assets include eight distributables, a CycloneDX SBOM, provenance and SHA256 checksums. An optional small installer MCPB and its separate checksum were subsequently added, bringing the total to thirteen. All asset hashes were verified. Directory review is separate from release publication; no third-party approval is claimed without a receipt.

| Channel | Distribution route | Current 1.0 status |
|---|---|---|
| GitHub Releases | Stable tag, three Navisworks add-in ZIPs, runtime ZIP, desktop ZIP, full/installer MCPBs, wheel/sdist, SBOM, provenance and checksums | Published; not a prerelease; source a780433; thirteen assets with verified digests |
| ChatGPT Work desktop / Codex | Local Personal marketplace and standalone runtime | Installed locally; Codex CLI installation and MCP protocol passed; Work connection to add-in 1.0.0.0 confirmed by the user |
| Claude Code | Repository plugin or direct stdio registration | Direct 1.0 connection and repeated registration verified |
| Claude Desktop | Standalone MCPB or local stdio configuration | Official bundle validation passed; connection to add-in 1.0.0.0 confirmed by the user |
| Claude desktop extension directory | [Official submission instructions](https://claude.com/docs/connectors/building/submission) | Submitted 2026-09-07 UTC; form confirmed receipt; awaiting Anthropic evaluation |
| Claude plugin directory | [Plugin submissions](https://platform.claude.com/plugins/submissions) | NavisCoord submitted for Claude Code on 2026-09-07 UTC; dashboard confirms pending review |
| PyPI | Versioned wheel and sdist, then authenticated upload or configured Trusted Publisher | Artifacts built; publication credentials/setup not established |
| Official MCP Registry | `server.json` references the public MCPB and its SHA256; GitHub namespace authentication | Published 2026-09-07 at 04:06 UTC; public API reports active/latest for io.github.HorizunGroup/naviscoord-mcp 1.0.0 |
| Glama | [Repository indexing and ownership verification](https://glama.ai/mcp/methodology) | GitHub sign-in completed in Chrome; Submit for Review action completed and form closed; no persistent receipt or public listing observed |
| MCPFly | [Repository submission](https://mcpserver.so/submit) | Submitted 2026-09-07 UTC; ID `HorizunGroup/naviscoord-mcp`; pending approval |
| MCP.so | [Paid submission](https://mcp.so/submit?type=server) | Current form requires a $39 payment; no purchase made |
| Awesome MCP Servers | [Free submission](https://mcpservers.org/submit) | Submitted 2026-09-07 UTC; page confirmed Submission Successful; review pending |
| Smithery | [Local installer MCPB](https://smithery.ai/servers/pabloalejandrozg/naviscoord-mcp) | Published; deployment SUCCESS; public API lists 53 tools; public download hash verified |

## Submission receipts

**Official MCP Registry — 2026-09-07 UTC.** Publication succeeded under the organization namespace. The public [version API](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.HorizunGroup%2Fnaviscoord-mcp/versions/1.0.0) reports `status: active`, `isLatest: true`, version 1.0.0 and the final GitHub MCPB checksum. The initial device-login token could publish only to the personal namespace; using the authenticated owner's supported token-exchange route resolved organization authorization without changing account visibility or project identity. Registry publication verifies namespace ownership, not engineering correctness or vendor certification.

**Smithery — 2026-09-07 UTC.** Deployment `5c195cc7-9ccc-4acd-800d-0ba696a64485` returned `SUCCESS`. The public page is marked Local, its API lists 53 tools, and its unauthenticated download matched SHA256 `94f54a87f3ea8de8286e8569cadf9bc96bd649ccb0367346b625e47af106bcc7`. Smithery limits bundles to 25 MB, so its installer bundle uses the approved v1.0.0 PowerShell launcher to obtain the complete runtime. A clean-cache installation passed MCP initialization and tool discovery; all 180 runtime files matched the full public bundle. A subsequent launch with downloads disabled passed live Navisworks health. CLI 4.11.1 incorrectly forwards abbreviated MCPB tool entries as a server card, while its API requires input schemas. The publishing script submits a separate complete card generated from the verified executable, preserving a valid MCPB manifest.

**Stable release and submission update — 2026-09-07 UTC.** GitHub release publication is confirmed, and an unauthenticated download of the MCPB matched SHA256 `a94cd1dc2f5532f9830a4fd857f16cc5c36f761465b8aa5de67ab76b2971d01e`. The existing Anthropic desktop-extension response was edited and accepted again with **Your response has been recorded**. Its feedback now points reviewers to the final public MCPB and identifies the original attachment as superseded; the response-edit interface did not expose a usable file-replacement control. No duplicate response was created. The Claude plugin dashboard still shows pending review and exposes no edit action. Glama search still shows no NavisCoord listing.

**Awesome MCP Servers — 2026-09-07 UTC.** The free form accepted NavisCoord by HorizunGroup under Design, with the public repository and supported local-client description. Its confirmation page displayed **Submission Successful!** and stated that review would occur within 12 hours; this is the directory's stated estimate, not a guarantee. No paid promotion was selected.

The records below describe the original submissions before stable publication.

**Anthropic desktop extensions — 2026-09-07 UTC.** After the publisher authorized the review contact and directory terms, the official MCPB form accepted `naviscoord-1.0.0-win-x64.mcpb` and displayed **“Your response has been recorded.”** The form stated that a response copy would be emailed to the authorized contact. The submission identifies the bundled Python runtime, the independent relationship to Autodesk, the public source PR, the privacy policy and the pending independent merge review. It does not claim that stable release publication or directory approval has occurred.

Submitted runtime source: `0a10f4fc5f0fd3398800f6dfd6d32d415ddeaddc`. MCPB SHA256: `bb99934a31490eb5d1751d8b45fbc6c109752036fcb68bf4de0d02978b58a6eb`. The subsequent desktop-installer change does not alter this MCPB, which contains the standalone runtime. The personal contact and editable-response link are intentionally excluded from public documentation.

**MCPFly — 2026-09-07 UTC.** The form accepted the public GitHub repository as an MCP server and displayed **“Submission received and pending approval”**, with submission ID `HorizunGroup/naviscoord-mcp`. This is a repository-listing request, not a claim that a 1.0 package is already publicly downloadable.

**Claude plugin directory — 2026-09-07 UTC.** The authenticated HORIZUN GROUP console displayed **“Plugin submitted for review”**. The submissions dashboard then listed **NavisCoord — Submitted and pending review**, with the submitted description identifying the 1.0 release PR and pending independent merge approval. Claude Code was selected as the tested surface; Cowork was not claimed. The MIT license, public privacy policy and authorized review contact were provided. This submission complements the separate Claude Desktop MCPB submission above.

**Glama — 2026-09-07 UTC.** Following the publisher's instruction to use Chrome, GitHub authentication completed as the repository maintainer. The open-source Server form was filled with the public repository, NavisCoord name and a description identifying the local Navisworks prerequisite and pending 1.0 release review. Submit for Review closed the form without a visible error. No persistent receipt was exposed and the repository did not appear in search or at its expected profile URL when checked. This record therefore documents the submission action, not confirmed ingestion or approval; no duplicate submission was sent.

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

To reproduce the small installer after downloading and verifying the full release bundle and its matching portable runtime:

```powershell
python scripts/build_bootstrap_mcpb.py dist/naviscoord-1.0.0-win-x64.mcpb
mcpb pack dist/mcpb-bootstrap dist/naviscoord-1.0.0-win-x64-installer.mcpb
```

The builder reads launcher files from the immutable version tag, verifies the local runtime against every bundled runtime file, and obtains the real tool schemas through MCP stdio. `scripts/publish_smithery.py <bundle> <namespace/server>` uses the documented release API with `SMITHERY_API_KEY` provided through the environment; never put that credential in repository files. The installer bundle uses its own adjacent checksum so the original release checksum file remains unchanged.

The [official MCP Registry quickstart](https://modelcontextprotocol.io/registry/quickstart) requires the referenced package to be published first. The [MCPB package route](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/package-types.mdx) accepts a GitHub release download with `registryType: mcpb` and `fileSha256`. The final metadata uses that route under `io.github.HorizunGroup/naviscoord-mcp`; PyPI publication is independent. If adding PyPI later, verify the README ownership marker and the declared package version before upload.

Claude's [official submission page](https://claude.com/docs/connectors/building/submission) distinguishes local desktop MCPB submissions from the remote connector portal. Local bundles have a separate form and require a public privacy policy and annotated tools. A successful form submission is pending review, not certification. Record the actual submission URL or receipt and status here when available.

For each directory, verify the repository is not already listed, submit only supported local-client capabilities, and retain a record of the outcome. Do not manufacture public endpoints, Windows signing trust, approval badges or zero-defect guarantees to meet a form's requirements.
