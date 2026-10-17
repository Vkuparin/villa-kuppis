# Setup record

Date: 17 October 2026.

## Template adaptation

Read the owner's private project-template README, AGENTS, SETUP, workflow documentation and bootstrap script. Started a fresh Git history from its explicit scaffold file list. No template history, private task history, machine configuration, credentials or binaries are included.

The normal bootstrap requires private GitHub creation, a completed product interview and a shared dev-dashboard installation. The owner explicitly requested a public repository, tentative docs and questions after initial findings/setup. Those instructions take precedence. The scaffold was rendered directly without falsely declaring that product questions are settled or that the stock bootstrap succeeded. The project is not an implemented app.

Retained coordinator, dashboard and agent launcher scripts. Adjusted project names, commands and the public-publication instruction. The Windows paths in shared-tool defaults are inherited setup conventions, not dependencies of the future app. Cloud execution here has Node 24.19.0; no final app runtime is selected.

## Current state

- Local repository: `/workspace/villa-kuppis`, branch `main`.
- Remote: [Vkuparin/villa-kuppis](https://github.com/Vkuparin/villa-kuppis), public, created by the owner. Its initial commit `866048c12d6afe2696781eaa6b981ecb73548131` contains Apache License 2.0; preserve it.
- GitHub connector reads and repository write permissions are confirmed. The connector has no repository-create action; the owner has now resolved that blocker. Publication progress is tracked in F1.2.
- Shell network access was denied in the default sandbox. Additional-network requests did not complete and were interrupted. No server response established whether the CLI credential would work with access; the initial auth check alone is not conclusive.
- No callable browser-control tool was exposed, despite the browser skill being available. Web research used the search/fetch tool; no user browser session was accessed.
- REA was not executed; no live-device actions were taken.
- Shared dev-dashboard and Hermes/Kynsi are not available in this cloud workspace. Do not claim delegation or full dashboard validation.

## Publication method

Use the connected GitHub Git-data API to publish the reviewed current files as a commit descending from the owner's existing license commit. Shell Git networking is unavailable. Preserve all existing remote files, update `main` only against its observed head, and verify the complete remote tree against local file hashes. The owner's existing request authorizes publication; no second visibility approval is needed.

Earlier task references such as `c342e37` and `df7b0f4` identify local draft history, not remote commits. Preserve that history separately while aligning the main checkout with the connector-created remote history. No private template history is published. Do not paste tokens into chat or commit them.

## Checks

- Passed JavaScript syntax checks for all five inherited `.mjs` scripts.
- Passed a scan of all 24 project files for unresolved template markers and local Markdown link targets; parsed Claude settings as JSON.
- Reviewed the explicit file list and scanned for common secret markers and private template task-history fragments; no matches found. This is a bounded check, not a guarantee from a secret-scanning service.
- `node scripts/check-docs.mjs` failed because the shared dev-dashboard parser is not installed. Full template document validation remains pending on the owner's configured machine.
- No application tests, browser UI tests or live hardware checks were run; no application exists yet.
- Staged whitespace check passed before the initial commit `c342e37`.
- Coordinator status could not run: this sandbox rejected its Node-to-Git subprocess with `spawnSync git EPERM`. No coordinator lease was acquired; there is no lease to release. Direct Git commands worked.

No tests can establish hardware compatibility from this workspace.

## Follow-up research

Continued after the owner accepted Google's cottage infrastructure and confirmed Android devices. Added a platform decision and experiment specifications, including a separately paid Home MCP candidate for the PC. Checked local links and unresolved template markers across 18 Markdown files, and staged whitespace checks passed. No runtime code, live-device action or paid service was introduced. The coordinator subprocess and shell network failures were rechecked and remain unchanged. A connector repository search still returned no accessible `villa-kuppis` repository; the owner was asked for an empty public remote or a session with working CLI access.
