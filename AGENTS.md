# Project

Project: Villa Kuppis.

Purpose: A personal Finnish-language home automation app for local device control at home and at a cottage.

Stack: Design phase. Own service on the home Windows PC with SQLite and reusable device adapters (Python or TypeScript, undecided); no Home Assistant or VM unless last resort. Browser and Android UI; cottage devices through Google Home over Matter. Update this line when the runtime, framework or key libraries change.

Codex, Hermes and Claude Code (through `CLAUDE.md`) all read this file. This is a hobby project: move quickly, keep process small, and ask the owner only about real product choices.

## Everyone

- Work in development mode with an isolated temporary data directory. Never use the owner's live accounts, personal data or paid AI allowance for testing; a live test needs the owner's explicit go-ahead.
- Test what you change: run the affected tests, add one when new behavior has none, and mock external network boundaries. Focus on core use cases and likely failures. Avoid speculative edge-case work and extra process; skip full-suite runs and extra documents unless asked.
- Report only checks you actually ran. Write plainly; apply the unslop skill when available.
- Keep private reference material in ignored `scratch/`. Never commit credentials or personal data.
- Never reset, stash or overwrite work you did not make. Stage only your own changes.

| Purpose | Command |
| --- | --- |
| Run in development mode with isolated data | Not implemented; use synthetic devices and an isolated temporary data directory |
| Tests | Not applicable to the documentation-only scaffold; define before implementation |
| Type, build and format checks | node scripts/check-docs.mjs (requires shared dev-dashboard); git diff --check |

Define a command here before first relying on it. Add rows when the project needs them.

## Workers

An assignment with owned files wins over the rest of this file. Edit only those files and run the named checks. Do not commit, push or edit task cards. Return what changed, the checks you ran and what is unresolved.

## Coordinators

You coordinate when the owner starts you in this checkout. There is one coordinator at a time.

1. **Start.** Run `node scripts/coordinate.mjs status` for the lease, branch, uncommitted files, recent commits, worktrees and cards. Then run `node scripts/coordinate.mjs takeover --owner <harness>:<thread>`. The owner starting you authorizes replacing a lease left behind; ask only if the owner said two coordinators are running.
2. **Route.** Scripts do mechanical work. Kynsi, the owner's persistent Hermes agent, takes local work with a clear specification and a check (Tier 3, and Tier 2 kinds it has handled before). Use `hermes.agent_connection`, `agent_request` and `agent_request_status`; “local workers,” “local agent” and “Hermes” all mean Kynsi. Never spawn a stateless local worker, a separate Hermes profile or a local subagent, or call the model directly. Kynsi uses Hermes's selected local model. Respect local_runtime.enabled: when false, leave it off and use the lowest-cost capable paid model available in the calling Codex/Claude harness, or the current coordinator if model selection is unavailable. This serial fallback is authorized within scope; explicit local-only tasks wait. While enabled, a real `agent_request` can restore the existing stopped gateway and wake the selected downloaded model; opening a harness and connection checks do neither. Never restart an active gateway. Review partial work before paid continuation if the switch turns off during a task. Keep design, diagnosis and anything ambiguous (Tier 1). Review Kynsi's changes and checks; give one concrete repair request when needed, then finish the task yourself if it still fails. Start parallel paid subagents only when the owner asks for parallel work.
3. **Stay resumable.** A session can end without warning. Write the card's `In progress` line when you start and commit after every passing step; WIP commits are fine. The next coordinator resumes from `status`, the card and Git.
4. **Finish.** Fetch, commit and push to the project remote; this is authorized. The owner explicitly requested a public repository. Publish only reviewed project files; never publish private template history, credentials, captures or personal deployment configuration. Do not change visibility without an explicit owner request. Mark the card `Done` with the commit and checks run, then release the lease.

Details: [docs/agents.md](docs/agents.md). Goals and decisions: [docs/roadmap.md](docs/roadmap.md). Cards: [docs/tasks/](docs/tasks/README.md).
