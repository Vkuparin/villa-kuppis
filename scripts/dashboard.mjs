// Usage: node scripts/dashboard.mjs [--port NUMBER] [--open]
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { resolveDashboardFile } from "./tooling.mjs";

try {
  const args = process.argv.slice(2);
  let port;
  let open = false;
  for (let index = 0; index < args.length; index++) {
    if (args[index] === "--open") open = true;
    else if (args[index] === "--port") {
      const value = args[++index];
      port = Number(value);
      if (!/^\d+$/.test(value || "") || !Number.isInteger(port) || port < 1 || port > 65535) throw new Error("--port needs a number between 1 and 65535.");
    } else throw new Error("Usage: node scripts/dashboard.mjs [--port NUMBER] [--open]");
  }
  const server = resolveDashboardFile("scripts/dashboard/server.mjs");
  const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
  const child = spawn(process.execPath, [server, "--root", root, ...(open ? ["--open"] : [])], {
    stdio: "inherit",
    windowsHide: true,
    env: { ...process.env, ...(port ? { DEVELOPMENT_DASHBOARD_PORT: String(port) } : {}) },
  });
  child.once("error", (error) => {
    console.error(`ERROR: ${error.message}`);
    process.exitCode = 1;
  });
  child.once("exit", (code, signal) => {
    process.exitCode = code ?? (signal ? 1 : 0);
  });
} catch (error) {
  console.error(`ERROR: ${error.message}`);
  process.exitCode = 1;
}
