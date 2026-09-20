import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export interface BridgeConfig {
  codesysExe: string;
  codesysProfile: string;
  bridgeDir: string;
  timeoutMs: number;
}

export interface BridgeTask {
  op: string;
  args: Record<string, unknown>;
}

export interface BridgeResult {
  ok: boolean;
  error: string | null;
  data: unknown;
}

const DEFAULT_PROFILE = "CODESYS V3.5 SP21 Patch 1";
const EXE_SEARCH_ROOTS = ["C:\\Program Files\\CODESYS", "C:\\Program Files (x86)\\CODESYS"];

/** Locate CODESYS.exe: env override first, then a shallow search under the
 * usual install roots. Returns "" when nothing is found (callers report a
 * clear error instead of launching a bogus path). */
function findCodesysExe(dir: string, depth: number): string | null {
  if (depth > 3) return null;
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return null;
  }
  for (const e of entries) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      const hit = findCodesysExe(p, depth + 1);
      if (hit) return hit;
    } else if (e.name.toLowerCase() === "codesys.exe") {
      return p;
    }
  }
  return null;
}

function resolveCodesysExe(): string {
  if (process.env.CODESYS_EXE) return process.env.CODESYS_EXE;
  for (const root of EXE_SEARCH_ROOTS) {
    const hit = findCodesysExe(root, 0);
    if (hit) return hit;
  }
  return "";
}

function moduleRoot(): string {
  // <root>/src/mcp/bridge.ts  or  <root>/dist/src/mcp/bridge.js
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
}

export function loadBridgeConfig(): BridgeConfig {
  const root = moduleRoot();
  return {
    codesysExe: resolveCodesysExe(),
    codesysProfile: process.env.CODESYS_PROFILE ?? DEFAULT_PROFILE,
    bridgeDir: process.env.CODESYS_BRIDGE_DIR ?? path.join(root, "bridge"),
    timeoutMs: Number.parseInt(process.env.CODESYS_BRIDGE_TIMEOUT_MS ?? "240000", 10)
  };
}

let queue: Promise<unknown> = Promise.resolve();

export function runBridgeTaskSerial(task: BridgeTask): Promise<BridgeResult> {
  // Serialize calls: a single CODESYS headless instance handles one task at a time.
  const run = queue.then(() => runBridgeTask(task));
  queue = run.catch(() => undefined);
  return run;
}

// ---------------------------------------------------------------------------
// Warm-daemon routing: if bridge_daemon.py is alive (fresh daemon_ping.json),
// submit the task to its queue and let the long-lived CODESYS session execute
// it (~1-3 s) instead of spawning a new CODESYS process (~20-90 s per call).
// ---------------------------------------------------------------------------

let seq = 0;

function daemonPingPath(): string {
  return path.join(loadBridgeConfig().bridgeDir, "daemon_ping.json");
}

function queueDir(): string {
  return path.join(loadBridgeConfig().bridgeDir, "queue");
}

export function daemonAlive(maxAgeMs = 5000): boolean {
  try {
    const ping = JSON.parse(readFileSync(daemonPingPath(), "utf8")) as { ts?: number; status?: string };
    // a "stopped" ping may be fresh (written on graceful exit) but the daemon is gone
    return (
      typeof ping.ts === "number" &&
      ping.status !== "stopped" &&
      Date.now() / 1000 - ping.ts < maxAgeMs / 1000
    );
  } catch {
    return false;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Best-effort detached start of bridge_daemon.py; resolves when the daemon
 * answers its first ping or after the startup budget is exhausted (caller
 * then falls back to the cold spawn path). */
async function ensureDaemonStarted(config: BridgeConfig): Promise<void> {
  try {
    const daemonPy = path.join(config.bridgeDir, "bridge_daemon.py");
    if (!existsSync(daemonPy)) {
      return;
    }
    // CODESYS' argument parser requires --profile="<name>" (quotes around the
    // value only), so drive it through a generated batch file.
    const cmdFile = path.join(config.bridgeDir, "bridge_daemon_run.cmd");
    writeFileSync(
      cmdFile,
      [
        "@echo off",
        `"${config.codesysExe}" --noUI --profile="${config.codesysProfile}" --runscript="${daemonPy}"`,
        ""
      ].join("\r\n"),
      "utf8"
    );
    const child = spawn(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", cmdFile], {
      windowsHide: true,
      detached: true
    });
    child.unref();
    const t0 = Date.now();
    while (Date.now() - t0 < 120000) {
      if (daemonAlive(10000)) {
        return;
      }
      await sleep(1000);
    }
  } catch {
    // fall through: caller uses the cold spawn path
  }
}

async function runBridgeTaskViaDaemon(task: BridgeTask, timeoutMs: number): Promise<BridgeResult> {
  const qdir = queueDir();
  mkdirSync(qdir, { recursive: true });
  const id = `mcp-${Date.now()}-${process.pid}-${++seq}`;
  const taskPath = path.join(qdir, `${id}.json`);
  const resultPath = path.join(qdir, `${id}.result.json`);
  rmSync(resultPath, { force: true });
  writeFileSync(taskPath, JSON.stringify(task), "utf8");
  const t0 = Date.now();
  try {
    while (Date.now() - t0 < timeoutMs) {
      if (existsSync(resultPath)) {
        // let the daemon finish writing, then read
        await sleep(50);
        return JSON.parse(readFileSync(resultPath, "utf8")) as BridgeResult;
      }
      await sleep(150);
    }
    throw new Error(`warm daemon did not answer within ${timeoutMs} ms`);
  } finally {
    rmSync(taskPath, { force: true });
    rmSync(resultPath, { force: true });
  }
}

export async function runBridgeTask(task: BridgeTask): Promise<BridgeResult> {
  const config = loadBridgeConfig();
  if (!daemonAlive() && process.env.CODESYS_BRIDGE_AUTOSTART !== "0") {
    // no warm daemon yet: start one in the background and wait for its ping.
    // First call of a session pays the startup (~2-25 s), all later calls are hot.
    await ensureDaemonStarted(config);
  }
  if (daemonAlive()) {
    try {
      const result = await runBridgeTaskViaDaemon(task, config.timeoutMs);
      if (result && typeof result === "object" && "ok" in result) {
        return result;
      }
    } catch (err) {
      // Daemon died mid-task -> safe to fall back to a cold spawn. Daemon
      // alive but silent -> do NOT spawn a second CODESYS: it would fight
      // over the same project file lock.
      if (daemonAlive(15000)) {
        throw new Error(
          `Warm daemon accepted the task but did not answer (${(err as Error).message}). ` +
            "It may be stuck on a long build; wait and retry, or stop it with " +
            "'python bridge_client.py stop' and retry (cold fallback)."
        );
      }
    }
  }
  return runBridgeTaskCold(task, config);
}

async function runBridgeTaskCold(task: BridgeTask, config: BridgeConfig): Promise<BridgeResult> {
  if (!config.codesysExe) {
    return {
      ok: false,
      error:
        "CODESYS.exe not found. Set the CODESYS_EXE env var to the full path of CODESYS.exe " +
        "(e.g. C:\\Program Files\\CODESYS\\...\\CODESYS.exe).",
      data: null
    };
  }
  const bridgePy = path.join(config.bridgeDir, "bridge.py");
  if (!existsSync(bridgePy)) {
    return {
      ok: false,
      error: `bridge.py not found at ${bridgePy}`,
      data: null
    };
  }
  if (!existsSync(config.bridgeDir)) {
    mkdirSync(config.bridgeDir, { recursive: true });
  }

  const taskFile = path.join(config.bridgeDir, "task.json");
  const resultFile = path.join(config.bridgeDir, "result.json");
  const cmdFile = path.join(config.bridgeDir, "bridge.cmd");
  rmSync(resultFile, { force: true });
  writeFileSync(taskFile, JSON.stringify(task, null, 2), "utf8");
  // CODESYS' argument parser requires --profile="<name>" (quotes around the value
  // only), so drive it through a generated batch file instead of spawn args.
  writeFileSync(
    cmdFile,
    [
      "@echo off",
      `"${config.codesysExe}" --noUI --profile="${config.codesysProfile}" --runscript="${bridgePy}"`,
      ""
    ].join("\r\n"),
    "utf8"
  );

  await new Promise<void>((resolve, reject) => {
    const child = spawn(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", cmdFile], {
      windowsHide: true
    });
    let stderr = "";
    child.stderr?.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`CODESYS bridge timed out after ${config.timeoutMs} ms. ${stderr}`));
    }, config.timeoutMs);
    child.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`Failed to start CODESYS: ${err.message}`));
    });
    child.on("close", () => {
      clearTimeout(timer);
      resolve();
    });
  });

  if (!existsSync(resultFile)) {
    return {
      ok: false,
      error:
        "CODESYS finished without writing result.json. Check that the ScriptEngine is installed and the profile name is correct.",
      data: null
    };
  }

  try {
    return JSON.parse(readFileSync(resultFile, "utf8")) as BridgeResult;
  } catch (err) {
    return {
      ok: false,
      error: `Failed to parse result.json: ${(err as Error).message}`,
      data: null
    };
  }
}
