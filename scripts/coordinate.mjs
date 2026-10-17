import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { closeSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { hostname } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

function git(root, ...args) {
  return execFileSync("git", ["-C", root, ...args], { encoding: "utf8", windowsHide: true }).trim();
}

export function leasePaths(root = process.cwd()) {
  const projectRoot = git(resolve(root), "rev-parse", "--show-toplevel");
  const commonDir = resolve(projectRoot, git(projectRoot, "rev-parse", "--git-common-dir"));
  const directory = join(commonDir, "agent-workflow");
  return { projectRoot, directory, lease: join(directory, "coordinator.json"), guard: join(directory, "coordinator-operation.lock") };
}

function identifier(value, label) {
  if (typeof value !== "string" || !value.trim() || /[\r\n\0]/.test(value)) {
    throw new Error(`${label} must be a nonempty identifier, such as codex:thread-123.`);
  }
  return value.trim();
}

function readLease(paths) {
  if (!existsSync(paths.lease)) return null;
  let value;
  try {
    value = JSON.parse(readFileSync(paths.lease, "utf8"));
  } catch (error) {
    throw new Error(`Cannot read coordinator lease ${paths.lease}: ${error.message}. Do not replace it without owner instruction.`);
  }
  if (value?.version !== 1 || typeof value.owner !== "string" || !value.owner.trim()) {
    throw new Error(`Invalid coordinator lease ${paths.lease}. Do not replace it without owner instruction.`);
  }
  return value;
}

function mutate(paths, owner, action) {
  mkdirSync(paths.directory, { recursive: true });
  let descriptor;
  try {
    descriptor = openSync(paths.guard, "wx");
  } catch (error) {
    if (error.code === "EEXIST") {
      throw new Error(`A coordinator operation is locked at ${paths.guard}. Inspect status; a crashed operation requires owner-directed recovery.`);
    }
    throw error;
  }
  let result;
  try {
    writeFileSync(descriptor, JSON.stringify({ owner, pid: process.pid, host: hostname(), started_at: new Date().toISOString() }) + "\n");
    result = action();
  } finally {
    closeSync(descriptor);
    unlinkSync(paths.guard);
  }
  if (result && Object.hasOwn(result, "operation_in_progress")) result.operation_in_progress = false;
  return result;
}

function publish(paths, lease) {
  const temporary = join(paths.directory, `coordinator-${randomUUID()}.tmp`);
  try {
    writeFileSync(temporary, JSON.stringify(lease, null, 2) + "\n", { flag: "wx" });
    renameSync(temporary, paths.lease);
  } finally {
    if (existsSync(temporary)) unlinkSync(temporary);
  }
}

function requireOwner(lease, owner) {
  if (!lease) throw new Error("No coordinator lease is held.");
  if (lease.owner !== owner) throw new Error(`Coordinator lease belongs to ${lease.owner}; ${owner} cannot change it.`);
}

export function coordinatorStatus(root = process.cwd()) {
  const paths = leasePaths(root);
  const lease = readLease(paths);
  return { state: lease ? "held" : "free", ...lease, lease_path: paths.lease, operation_in_progress: existsSync(paths.guard) };
}

export function acquireCoordinator(root, owner, scope = "") {
  owner = identifier(owner, "Owner");
  const paths = leasePaths(root);
  return mutate(paths, owner, () => {
    const existing = readLease(paths);
    if (existing) {
      requireOwner(existing, owner);
      return coordinatorStatus(root);
    }
    const now = new Date().toISOString();
    publish(paths, {
      version: 1, owner, scope, project_root: paths.projectRoot,
      base_commit: git(paths.projectRoot, "rev-parse", "HEAD"),
      acquired_at: now, updated_at: now, host: hostname(),
    });
    return coordinatorStatus(root);
  });
}

export function releaseCoordinator(root, owner) {
  owner = identifier(owner, "Owner");
  const paths = leasePaths(root);
  return mutate(paths, owner, () => {
    requireOwner(readLease(paths), owner);
    unlinkSync(paths.lease);
    return { state: "free", lease_path: paths.lease };
  });
}

export function handoffCoordinator(root, owner, nextOwner, scope) {
  owner = identifier(owner, "Owner");
  nextOwner = identifier(nextOwner, "Next owner");
  const paths = leasePaths(root);
  return mutate(paths, owner, () => {
    const lease = readLease(paths);
    requireOwner(lease, owner);
    publish(paths, { ...lease, owner: nextOwner, previous_owner: owner, scope: scope ?? lease.scope, updated_at: new Date().toISOString() });
    return coordinatorStatus(root);
  });
}

// The owner starting a new coordinator is the authorization to replace a lease
// whose holder ended without releasing it, for example at a usage limit.
export function takeoverCoordinator(root, owner, scope) {
  owner = identifier(owner, "Owner");
  const paths = leasePaths(root);
  return mutate(paths, owner, () => {
    const lease = readLease(paths);
    if (lease?.owner !== owner) {
      const now = new Date().toISOString();
      publish(paths, {
        version: 1, owner, ...(lease ? { previous_owner: lease.owner } : {}), scope: scope ?? lease?.scope ?? "",
        project_root: paths.projectRoot, base_commit: git(paths.projectRoot, "rev-parse", "HEAD"),
        acquired_at: now, updated_at: now, host: hostname(),
      });
    }
    return coordinatorStatus(root);
  });
}

const cardId = /[A-Z]+\d+(?:\.\d+)*/g;

function taskCards(projectRoot) {
  const directory = join(projectRoot, "docs", "tasks");
  if (!existsSync(directory)) return { in_progress: [], needs_decision: [], ready: [] };
  const cards = [];
  for (const file of readdirSync(directory).filter((name) => name.endsWith(".md") && name !== "README.md").sort()) {
    for (const line of readFileSync(join(directory, file), "utf8").split(/\r?\n/)) {
      const cells = line.split(/(?<!\\)\|/).slice(1, -1).map((cell) => cell.trim());
      if (cells.length < 5 || cells[0].match(cardId)?.[0] !== cells[0]) continue;
      cards.push({ id: cells[0], file: `docs/tasks/${file}`, task: cells[1], agent: cells[2], depends: cells[3].match(cardId) || [], status: cells[4] });
    }
  }
  const done = new Set(cards.filter((card) => /^Done\b/i.test(card.status)).map((card) => card.id));
  const short = (text) => text.length > 140 ? `${text.slice(0, 137)}...` : text;
  return {
    in_progress: cards.filter((card) => /^In progress\b/i.test(card.status)).map(({ id, file, agent, status }) => ({ id, file, agent, status })),
    needs_decision: cards.filter((card) => /^Needs decision\b/i.test(card.status)).map(({ id, file, status }) => ({ id, file, status })),
    ready: cards.filter((card) => /^Planned\b/i.test(card.status) && card.depends.every((id) => done.has(id))).map(({ id, file, agent, task }) => ({ id, file, agent, task: short(task) })),
  };
}

// Everything a coordinator needs to resume without the previous session's notes.
export function checkoutSummary(root = process.cwd()) {
  const { projectRoot } = leasePaths(root);
  const read = (...args) => {
    try {
      return execFileSync("git", ["-C", projectRoot, ...args], { encoding: "utf8", windowsHide: true, stdio: ["ignore", "pipe", "ignore"] }).split(/\r?\n/).filter(Boolean);
    } catch {
      return [];
    }
  };
  return {
    branch: read("status", "--short", "--branch")[0]?.replace(/^## /, "") ?? "",
    uncommitted: read("status", "--short"),
    recent_commits: read("log", "-5", "--format=%h %ad %s", "--date=short"),
    worktrees: read("worktree", "list"),
    cards: taskCards(projectRoot),
  };
}

export function runCli(args = process.argv.slice(2)) {
  const usage = "Usage: coordinate.mjs status|acquire|takeover|handoff|release [--root PATH] --owner ID [--to ID] [--scope TEXT].";
  const [command, ...rest] = args;
  const options = {};
  for (let index = 0; index < rest.length; index += 2) {
    const key = rest[index];
    if (!["--root", "--owner", "--to", "--scope"].includes(key) || !rest[index + 1] || rest[index + 1].startsWith("--")) {
      throw new Error(`Invalid option ${key ?? ""}. ${usage}`);
    }
    if (Object.hasOwn(options, key)) throw new Error(`Duplicate option ${key}.`);
    options[key] = rest[index + 1];
  }
  const root = options["--root"] || process.cwd();
  if (command === "status") {
    if (options["--owner"] || options["--to"] || options["--scope"]) throw new Error("Status accepts only --root.");
    return { ...coordinatorStatus(root), checkout: checkoutSummary(root) };
  }
  if (command !== "handoff" && options["--to"]) throw new Error("Only handoff accepts --to.");
  if (command === "release" && options["--scope"]) throw new Error("Release accepts only --root and --owner.");
  if (command === "acquire") return acquireCoordinator(root, options["--owner"], options["--scope"]);
  if (command === "takeover") return takeoverCoordinator(root, options["--owner"], options["--scope"]);
  if (command === "release") return releaseCoordinator(root, options["--owner"]);
  if (command === "handoff") return handoffCoordinator(root, options["--owner"], options["--to"], options["--scope"]);
  throw new Error(usage);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    console.log(JSON.stringify(runCli(), null, 2));
  } catch (error) {
    console.error(`ERROR: ${error.message}`);
    process.exitCode = 1;
  }
}
