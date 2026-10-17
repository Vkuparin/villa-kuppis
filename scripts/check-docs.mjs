// Usage: node scripts/check-docs.mjs [project-root]
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { resolveDashboardFile } from "./tooling.mjs";

try {
  if (process.argv.length > 3) throw new Error("Usage: node scripts/check-docs.mjs [project-root]");
  const root = resolve(process.argv[2] || process.cwd());
  const modelPath = resolveDashboardFile("scripts/dashboard/model.mjs");
  const { readDashboard } = await import(pathToFileURL(modelPath).href);
  const data = await readDashboard(root);
  for (const release of data.releases) {
    console.log(`${release.version}: ${release.counts.total} cards, ${release.counts.done} done, ${release.counts.active} in progress`);
  }
  console.log(`active release: ${data.activeRelease}; current release: ${data.currentRelease ? data.currentRelease.version : "none"}`);
  for (const issue of data.issues) console.error(`ISSUE ${issue.source}:${issue.line} ${issue.message}`);
  if (data.issues.length) process.exitCode = 1;
  else console.log("OK: no parse issues.");
} catch (error) {
  console.error(`ERROR: ${error.message}`);
  process.exitCode = 1;
}
