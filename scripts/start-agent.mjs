import { spawn } from 'node:child_process';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { access, readdir } from 'node:fs/promises';
import { loadToolingConfig } from './tooling.mjs';

export async function startAgent(harness, root = process.cwd(), selectedLocalModel) {
  if (!['codex', 'claude', 'hermes'].includes(harness)) throw new Error('Usage: node scripts/start-agent.mjs codex|claude|hermes [project-root]');
  const directory = resolve(root);
  const config = await loadToolingConfig();
  let command = config.harnesses?.[harness]?.command || harness;
  let args = config.harnesses?.[harness]?.args || [];
  if (harness === 'claude' && config.harnesses?.claude?.searchRoot) {
    const base = config.harnesses.claude.searchRoot;
    const versions = (await readdir(base, { withFileTypes: true })).filter(entry => entry.isDirectory() && /^\d+\.\d+\.\d+$/.test(entry.name)).map(entry => entry.name).sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
    let found;
    for (const version of versions) {
      for (const directory of await readdir(join(base, version), { withFileTypes: true })) {
        if (!directory.isDirectory()) continue;
        const candidate = join(base, version, directory.name, 'claude.exe');
        try { await access(candidate); found = candidate; break; } catch {}
      }
      if (found) break;
    }
    if (!found) throw new Error('Claude Code executable was not found. Open this project in the Claude app, or update harnesses.claude in the shared configuration.');
    command = found;
  }
  if (harness === 'hermes') {
    const runtime = config.persistentHermes?.runtimeRoot || config.localWorkers?.path;
    if (!runtime) throw new Error('Kynsi bridge is not installed. Set persistentHermes.runtimeRoot in the machine configuration; see docs/agents.md.');
    command = process.execPath;
    args = [join(runtime, 'project-agent.mjs'), directory, ...(selectedLocalModel ? [selectedLocalModel] : [])];
  } else if (harness === 'codex') args = [...args, '--cd', directory];
  const child = spawn(command, args, { cwd: directory, stdio: 'inherit', shell: false, windowsHide: false });
  const code = await new Promise((resolve, reject) => {
    child.once('error', error => reject(error.code === 'ENOENT' ? new Error(`${command} is not on PATH. Open this project in the ${harness === 'codex' ? 'Codex' : 'Claude'} app, or set harnesses.${harness}.command in the machine configuration.`) : error));
    child.once('exit', resolve);
  });
  process.exitCode = code || 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) startAgent(process.argv[2], process.argv[3], process.argv[4]).catch(error => { console.error(error.message); process.exitCode = 1; });
