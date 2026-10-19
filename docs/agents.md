# Agent workflow

Coordinator detail for [AGENTS.md](../AGENTS.md). Keep work resumable and review completed batches.

## Starting and resuming

Start persistent Kynsi with `node scripts/start-agent.mjs hermes`; an optional project root and selected local model ID follow. This uses its existing conversation, memory, skills and Telegram replies. Kynsi owns the lease, cards, checks and integration while coordinating. A paid Codex session does not need to stay open to supervise it.

You can still open Codex or assign Claude Code as coordinator in the project directory. The owner starting a harness authorizes taking over a stale lease. Run `node scripts/coordinate.mjs status`, inspect uncommitted work and the current card, then take the lease. Never overwrite another agent's work.

## Kynsi-led routing

| Tier | Task shape | Who does it |
| --- | --- | --- |
| Automation | Search, formatting, generated files and running checks | Scripts and tools |
| Tier 3 | Clear behavior, known files and a focused check | Kynsi implements and checks locally |
| Tier 2 | Bounded work under a settled interface | Kynsi implements; Sol 6.1 Light reviews the completed batch |
| Tier 1 | Architecture, shared interfaces, ambiguous behavior and difficult diagnosis | Sol 6.1 Medium |
| Owner | A real product choice or live acceptance | The owner |

Light means `gpt-6.1-sol` with reasoning effort `low`; Medium uses the same model with `medium`. Split work by judgment required, not file count. New API behavior, authentication and device recovery that need investigation belong in Tier 1.

Kynsi may call paid Codex agents as needed while coordinating an approved project, without asking for each call. Use the bridge's Codex handoff helper with ChatGPT-authenticated Codex CLI. These calls consume the owner's subscription usage limits, never API credits. Do not switch to an API key or another provider if authentication or limits fail. Record the blocker and keep completed work resumable. Hermes must never invoke Claude.

Work sequentially unless the owner requests parallel paid work. Give a Codex agent the root, goal, owned files, constraints and focused checks. It is a worker: it must not take the lease, edit cards, commit, push or delegate back to Hermes. Review requests are read-only. Kynsi integrates the reviewed result and publishes under the project's rules.

## Completed Tier 2 batches (Kynsi-led coordination)

A batch is one coherent change under a settled interface. Finish implementation and focused checks, then send the diff, check results and acceptance conditions to Light once. Do not pay for a reviewer to watch local progress or poll an idle agent.

Light returns actionable findings or approval. Kynsi makes one concrete local repair for findings and reruns the affected checks. Have Light verify the repaired batch before integration. If local repair still fails, Light can finish a bounded Tier 2 fix; use Medium when the failure exposes a Tier 1 question. Inspect existing changes before continuation, and never repeat an uncertain request under a fresh ID. Explicit local-only assignments rule out paid review and continuation; report that limitation.

## Owner-assigned Claude coordination

Claude's existing workflow stays unchanged. When the owner assigns it as coordinator, Claude handles design, diagnosis and Tier 1, delegates Tier 3 and familiar Tier 2 work to Kynsi, reviews changes and checks, gives one concrete repair request, then finishes unresolved work itself. Its launcher, settings, permissions and model selection remain unchanged. This does not authorize Hermes to launch Claude.

## Persistent local work and runtime switch

“Kynsi,” “Hermes,” “local agent” and “local workers” mean the same persistent agent. Never spawn stateless local workers, separate Hermes profiles or local subagents, or substitute direct model inference. Codex agents use the separate paid CLI handoff; Hermes's own inference remains on its selected local model.

Codex and Claude delegate local work through the `hermes` MCP:

1. Read `agent_connection` to inspect the runtime switch, selected model and gateway without inference. An offline gateway is recoverable through a real assignment while enabled; never restart an active gateway.
2. Submit `agent_request` with a stable request ID, root, goal, owned files, acceptance conditions, development settings and named checks. Set `local_only: true` for local-only work. A worker returns changes and check evidence without commits or card edits; a coordinator assignment explicitly grants the coordinator role.
3. A queued or running request stays active after a tool timeout. Read `agent_request_status` as needed without repeating submission. `messages_send` only sends a notification; it does not start work.
4. Review changes before integration or paid continuation. The gateway records the result and delivers the reply to Telegram; do not send a duplicate notification.

Respect `local_runtime.enabled` on submission, queue dispatch and every local inference call. False or missing means no local work, even if a model server is running. Never enable the switch, download a model or force a server restart. While enabled, a real task may start the existing stopped gateway and wake the selected downloaded model; metadata checks do neither. Changed model selection stops an admitted task. Normal coding turns have a 100-iteration runtime budget, distinct from small acceptance-test instructions.

When local work is disabled, leave it off. The calling Codex/Claude harness may use its lowest-cost capable paid model within scope, or its current coordinator when model selection is unavailable. If Kynsi was working, review partial files and checks before paid continuation; `fallback.requires_review` records this case. Explicit local-only work waits. Existing Codex jobs do not enable the local runtime.

## Lease, cards and publication

The advisory lease lives in the Git common directory, is shared by worktrees and does not expire:

```powershell
node scripts/coordinate.mjs status
node scripts/coordinate.mjs takeover --owner hermes:project-session --scope "first feature"
node scripts/coordinate.mjs handoff --owner hermes:project-session --to codex:thread-123
node scripts/coordinate.mjs release --owner codex:thread-123
```

`takeover` records the previous owner; `acquire` refuses another holder. Handoff and release require the current owner ID. Use `--root` for another checkout. Only the coordinator edits cards. Write `In progress` at the start and commit each passing step. The next coordinator resumes from status, cards and Git. Preserve owner changes and stage only your files.

Sequential work stays in one checkout. If the owner requests concurrent edits, prepare worktrees at a named base with separate owned files. Follow the project's publication and visibility rules. Never publish credentials, captures, personal deployment settings or private reference history.

## Shared machine tools

Machine settings live at `D:/AIstuff/agent-workflow/config.json`; `AGENT_WORKFLOW_CONFIG` selects another file. `persistentHermes` names the bridge and conversation. Personal connection details and Codex authentication stay outside projects. Assigning owned files is an instruction, not a filesystem sandbox.

Use `node D:/AIstuff/repos/local-workers/codex-handoff.mjs request REQUEST.json`, then `status ID` or `result ID`. The bridge README documents request fields and installation. The helper works from the existing Hermes terminal without restarting the active gateway. Missing authentication, usage limits or unavailable models are blockers to report, never reasons to call Claude or use API billing. `node scripts/dashboard.mjs --open` shows cards in the shared dashboard; setup is documented in `D:/AIstuff/repos/dev-dashboard`.
