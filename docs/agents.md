# Agent workflow

Coordinator detail for [AGENTS.md](../AGENTS.md). Keep assignments small and check the result.

## Starting a harness

Open Codex or Claude Code with this project's directory. To ask Kynsi to coordinate it, run `node scripts/start-agent.mjs hermes`; an optional project root and selected model ID follow. This sends a request to the existing persistent agent. Continue talking to Kynsi through Telegram or Desktop.

Any of the three can coordinate. They share Git, task cards and the coordinator lease. Kynsi keeps its personal memory and conversation across project assignments.

## Routing

| Tier | Task shape | Who does it |
| --- | --- | --- |
| Automation | Search, formatting, generated files, mechanical transforms, running checks | Scripts and tools |
| Tier 3 | Clear behavior, known files, an example and a focused check | Kynsi on Hermes's selected local model while enabled |
| Tier 2 | Bounded work under a settled interface, with some judgment | Kynsi for kinds it has handled before, otherwise the coordinator |
| Tier 1 | Ambiguous behavior, architecture, shared interfaces, hard diagnosis | The coordinator |
| Owner | A product decision or acceptance step | The owner |

Choose by capability and observed results, without fixing future model names. Good Kynsi assignments include tracing a named code path, a small validator or component following an example, a contained bug fix with a failing test, fixtures with agreed expectations, and repetitive call-site changes. A small file count does not make concurrency or ambiguous design easy.

“Ask Kynsi,” “ask Hermes,” “ask a local agent,” “local workers” and “do this locally” all mean persistent Kynsi. Never spawn a stateless local worker, a separate Hermes profile or a local subagent, or bypass Kynsi with a direct model call. “Local only” also rules out finishing the task with a paid model; report unresolved work to the owner.

## Delegating to Kynsi

Codex and Claude use the `hermes` MCP. Each harness starts its own small stdio bridge automatically. The existing Hermes gateway owns Kynsi's execution, memory and normal Telegram replies. The bridge is packaged as **Kynsi** in Codex and as a Claude Desktop `.mcpb` extension. The shared source folder retains its historical name `local-workers`.

1. Read `agent_connection` to check `local_runtime.enabled`, the gateway, persistent conversation and selected local model without inference, starting the gateway or waking the model. `gateway_offline` is recoverable: proceed with the real `agent_request`, which tries Hermes's detached gateway start for the existing profile while enabled. It never restarts an active gateway. If `status` is `local_disabled`, leave the runtime off and use the calling Codex/Claude harness's lowest-cost capable paid model. Use the current coordinator if selecting a smaller model is unavailable. This serial fallback is authorized within scope. An explicit local-only task waits. Native `model` overrides must match Hermes's selection.
2. Call `agent_request` with a stable `request_id` and a complete `message`: project root or prepared worktree, goal, owned files, acceptance conditions, development/test settings and focused checks. Identify the calling coordinator with `sender`; set `local_only: true` when the owner requires local-only work. For a delegated assignment, tell Kynsi to return changes and actual check evidence without committing or pushing.
3. The request enters Kynsi's existing conversation. It can queue behind another turn. Wait for the result or read `agent_request_status`; a tool timeout does not cancel the task. Never repeat an uncertain request under a new ID. Inspect its record and conversation first.
4. Review the changed files and checks. Give one concrete repair request if needed, then finish the task yourself if it still fails. Respect an explicit local-only constraint. Integrate and publish through the coordinator's normal flow.

Kynsi uses Hermes's selected local model, with no paid fallback inside Hermes or the bridge. The bridge respects `local_runtime.enabled` on submission, queue dispatch and every inference call. False or missing means no local work, even if a model server is still running. It never changes the switch, downloads a model or forces a server restart. While enabled, a real task may let Hermes wake its selected downloaded model; a connection check only reads metadata. If the switch turns off after execution starts, `fallback.requires_review` is true: inspect partial changes, checks and the request before paid continuation. Never replay stopped or uncertain work. Requests record their provider, model and endpoint; changed selection stops the task. Normal coding turns have a 100-iteration runtime budget. An iteration may call several tools. Tiny live-test instructions are separate from that normal budget. Personal memory and permissions stay with Kynsi; assigning owned files is an instruction, not a filesystem sandbox.

Use a coordinator-prepared worktree when simultaneous edits need isolation. It is still the same Kynsi working in that checkout. Give exact paths and dependency setup where needed. Sequential work can stay in the current checkout, including an explicitly assigned ignored `scratch/` file. Do not send unrelated private material.

The first-party `messages_send` posts an outgoing notification; it does not start work. `agent_request` starts a normal gateway turn with native activity and reply delivery. The owner sees assignments and replies in the shared Telegram conversation and can ask Kynsi about past work. Keep conversation keys and credentials in machine configuration outside the project.

## Kynsi as coordinator

Kynsi can coordinate small projects and well-specified cards itself. It follows this project's status, lease, card, check and publication rules in its existing conversation. It works sequentially instead of spawning local subagents. When a task is beyond it, it records what it tried and the next action in the card, then tells the owner. A Codex or Claude coordinator can pick up that record.

## Coordinator lease

The lease is an advisory note in the Git common directory that says who integrates and edits task cards. All worktrees see the same one. It never expires.

```powershell
node scripts/coordinate.mjs status
node scripts/coordinate.mjs takeover --owner claude:session-456 --scope "first feature"
node scripts/coordinate.mjs handoff --owner claude:session-456 --to codex:thread-123
node scripts/coordinate.mjs release --owner codex:thread-123
```

`takeover` claims a free lease or replaces another owner's and records `previous_owner`. `acquire` refuses when someone else holds the lease. `handoff` and `release` need the current owner ID. Pass `--root` to act on another checkout.

## Resuming and parallel work

Sessions can end at a usage limit without notes. Keep the record as work happens: the card's `In progress` line names the agent, branch and scope, and each passing step is committed. The next coordinator runs `status`, takes over the lease, reads the card and recent commits, then continues. Preserve uncommitted work shown by `status`.

One sequential task stays in the coordinator checkout. Concurrent assignments use separate worktrees at a named base commit and separate owned files. Only the coordinator edits task cards. Sequential paid fallback when local work is disabled is authorized within scope. Start parallel paid subagents only when the owner asks for parallel work.

## Shared machine tools

Machine configuration lives at `D:/AIstuff/agent-workflow/config.json`; `AGENT_WORKFLOW_CONFIG` selects another file. `persistentHermes` holds the bridge location and Kynsi connection. Projects hold none of those personal settings. `node scripts/dashboard.mjs --open` shows this checkout's roadmap and cards in the shared dashboard.

Setup and registration commands are in each tool's README under `D:/AIstuff/repos/local-workers` and `D:/AIstuff/repos/dev-dashboard`. If the `hermes` tools are missing, reconnect the MCP or start a fresh harness session. A real assignment can restore an offline gateway while enabled; opening the harness or checking its connection cannot. If startup fails, report the service error. A missing downloaded model needs the owner's selection. If local work is disabled, respect the switch and use the authorized calling-harness fallback; never enable it yourself. Never substitute a stateless worker.
