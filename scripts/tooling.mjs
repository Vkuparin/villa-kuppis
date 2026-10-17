import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";

export function toolingConfigPath(env = process.env) {
  const shared = resolve("D:/AIstuff/agent-workflow/config.json");
  const legacy = resolve(join(env.LOCALAPPDATA || join(homedir(), "AppData", "Local"), "agent-workflow", "config.json"));
  const requested = env.AGENT_WORKFLOW_CONFIG && resolve(env.AGENT_WORKFLOW_CONFIG);
  const samePath = (a, b) => process.platform === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b;
  if (requested && !samePath(requested, legacy)) return requested;
  return existsSync(shared) ? shared : requested || legacy;
}

export function loadToolingConfig({ env = process.env } = {}) {
  const file = toolingConfigPath(env);
  if (!existsSync(file)) {
    if (env.AGENT_WORKFLOW_CONFIG) throw new Error(`Workflow configuration not found: ${file}`);
    return {};
  }
  let config;
  try {
    config = JSON.parse(readFileSync(file, "utf8").replace(/^\uFEFF/, ""));
  } catch (error) {
    throw new Error(`Cannot read workflow configuration ${file}: ${error.message}`);
  }
  if (!config || typeof config !== "object" || Array.isArray(config) || config.version !== 1) {
    throw new Error(`Workflow configuration ${file} must be an object with version: 1.`);
  }
  return config;
}

export function resolveDashboardFile(relative, { env = process.env } = {}) {
  const config = loadToolingConfig({ env });
  const configured = env.AGENT_DASHBOARD_ROOT || config.dashboard?.path || "D:/AIstuff/repos/dev-dashboard";
  if (typeof configured !== "string" || !configured.trim()) {
    throw new Error("dashboard.path must be a nonempty directory path in the workflow configuration.");
  }
  const base = isAbsolute(configured)
    ? configured
    : resolve(dirname(toolingConfigPath(env)), configured);
  const file = resolve(base, relative);
  if (!existsSync(file)) {
    throw new Error(
      `Dashboard module not found: ${file}. Install dev-dashboard and set dashboard.path in ${toolingConfigPath(env)} or AGENT_DASHBOARD_ROOT.`,
    );
  }
  return file;
}
