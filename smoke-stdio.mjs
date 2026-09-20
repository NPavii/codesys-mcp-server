// Smoke test: stdio MCP handshake + tools/list + real bridge call.
import { spawn } from "node:child_process";

const proc = spawn(process.execPath, ["dist/src/cli.js", "--stdio"], {
  stdio: ["pipe", "pipe", "pipe"]
});
let buf = "";
const responses = [];
proc.stdout.on("data", (c) => {
  buf += c.toString("utf8");
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (line.startsWith("{")) responses.push(JSON.parse(line));
  }
});
proc.stderr.on("data", (c) => process.stderr.write("[stderr] " + c));

function send(msg) {
  proc.stdin.write(JSON.stringify(msg) + "\n");
}
function waitFor(id, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const iv = setInterval(() => {
      const r = responses.find((x) => x.id === id);
      if (r) {
        clearInterval(iv);
        resolve(r);
      } else if (Date.now() - t0 > timeoutMs) {
        clearInterval(iv);
        reject(new Error("timeout waiting for id=" + id));
      }
    }, 50);
  });
}

send({ jsonrpc: "2.0", id: 1, method: "initialize", params: {
  protocolVersion: "2024-11-05",
  capabilities: {},
  clientInfo: { name: "smoke", version: "0.0.1" }
}});
send({ jsonrpc: "2.0", method: "notifications/initialized" });

const init = await waitFor(1);
console.log("server:", init.result?.serverInfo?.name, init.result?.serverInfo?.version);

send({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
const tools = await waitFor(2);
const names = (tools.result?.tools ?? []).map((t) => t.name);
console.log("tools count:", names.length);
console.log("bridge tools:", names.filter((n) => n.startsWith("codesys_") && !["codesys_project_open","codesys_project_info","codesys_list_objects","codesys_get_object","codesys_create_pou","codesys_create_member","codesys_create_dut","codesys_create_gvl","codesys_set_code","codesys_save_project","codesys_writing_guidance"].includes(n)).join(", "));

// Real call through the warm daemon: list libraries of Project_One
send({ jsonrpc: "2.0", id: 3, method: "tools/call", params: {
  name: "codesys_lib_list",
  arguments: { project: "D:\\KimiData\\kimi\\Workspaces\\CoDeSyS\\Project\\Project_One\\Project_one.project" }
}});
const call = await waitFor(3, 60000);
console.log("lib_list ok:", call.result?.isError === false);
const txt = call.result?.content?.[0]?.text ?? "";
console.log("lib_list text (first 300):", txt.slice(0, 300));

proc.kill();
process.exit(0);
