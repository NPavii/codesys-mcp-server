import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { runBridgeTaskSerial } from "./bridge.js";

const projectPathSchema = z
  .string()
  .min(1)
  .describe("Absolute path to the .project file, e.g. D:/CodeSys Project/MyProject.project");

const libraryPathSchema = z
  .string()
  .min(1)
  .describe("Absolute path to the .library file, e.g. D:/Libs/MyLib.library");

const objectPathSchema = z
  .string()
  .min(1)
  .describe("Object path inside the project tree, e.g. 'Device/Plc Logic/Application/PRG_Main'.");

function asTextResult(value: Record<string, unknown>) {
  return {
    content: [
      {
        type: "text" as const,
        text: JSON.stringify(value, null, 2)
      }
    ],
    structuredContent: value
  };
}

const BRIDGE_NOTE =
  "If the warm daemon is running (bridge_daemon.cmd), calls take ~1-3 s; otherwise each call starts " +
  "a headless CODESYS instance, performs the operation, saves and closes the project (takes ~10-30 s). " +
  "The project must NOT be open in the CODESYS GUI at the same time (file lock). " +
  "Configuration via env: CODESYS_EXE, CODESYS_PROFILE, CODESYS_BRIDGE_DIR, CODESYS_BRIDGE_TIMEOUT_MS.";

async function runTask(op: string, args: Record<string, unknown>) {
  const result = await runBridgeTaskSerial({ op, args });
  return asTextResult({
    ...result,
    note: result.ok ? BRIDGE_NOTE : undefined
  });
}

type Schema = Record<string, z.ZodTypeAny>;

interface BridgeToolSpec {
  name: string;
  title: string;
  description: string;
  op: string;
  schema: Schema;
}

const p = projectPathSchema;
const lib = libraryPathSchema;
const obj = objectPathSchema;
const appPath = z
  .string()
  .min(1)
  .describe("Application path, e.g. 'Device/Plc Logic/Application'.");
const confirmSchema = z
  .boolean()
  .describe("Required acknowledgement for destructive operations. Pass true only after explicit user approval.");

const BRIDGE_TOOLS: BridgeToolSpec[] = [
  // ---------- build / eval / batch ----------
  {
    name: "codesys_build",
    title: "Build CODESYS Application",
    description: "Compile an application and return build messages with error/warning counts. " + BRIDGE_NOTE,
    op: "build",
    schema: { project: p, application: appPath }
  },
  {
    name: "codesys_eval",
    title: "Eval ScriptEngine Expression",
    description:
      "Evaluate a Python expression in the CODESYS ScriptEngine context of the project " +
      "(globals: proj, projects, system, librarymanager, online, find_obj, tree, get_project). " +
      "Expressions only — no statements; use lambda for complex logic. " + BRIDGE_NOTE,
    op: "eval",
    schema: {
      project: p,
      expr: z.string().min(1).describe("Python expression to evaluate."),
      no_save: z.boolean().optional().describe("Skip saving the project after eval. Defaults to true.")
    }
  },
  {
    name: "codesys_batch",
    title: "Run CODESYS Batch",
    description:
      "Execute several bridge ops in one ScriptEngine session (project is auto-filled and saved at the end). " +
      "Set dry_run to preview the plan without executing. " + BRIDGE_NOTE,
    op: "batch",
    schema: {
      project: p,
      steps: z
        .array(z.record(z.unknown()))
        .min(1)
        .describe('List of {"op": ..., "args": {...}} steps.'),
      continue_on_error: z.boolean().optional().describe("Continue after a failed step. Defaults to true."),
      dry_run: z.boolean().optional().describe("Return the planned steps without executing anything.")
    }
  },
  // ---------- safety ----------
  {
    name: "codesys_snapshot",
    title: "Snapshot CODESYS Project/Library",
    description: "Copy the .project/.library file to a timestamped snapshot folder before risky edits. " + BRIDGE_NOTE,
    op: "snapshot",
    schema: { project: z.string().min(1).describe("Absolute path to the .project or .library file.") }
  },
  {
    name: "codesys_snapshot_list",
    title: "List CODESYS Snapshots",
    description: "List available snapshots with creation times.",
    op: "snapshot_list",
    schema: {}
  },
  {
    name: "codesys_snapshot_restore",
    title: "Restore CODESYS Snapshot",
    description:
      "Restore a .project/.library file from a snapshot (closes the project first). Destructive: requires confirm. " +
      BRIDGE_NOTE,
    op: "snapshot_restore",
    schema: {
      project: z.string().min(1).describe("Absolute path to the .project or .library file to overwrite."),
      snapshot: z.string().min(1).describe("Snapshot name from codesys_snapshot_list."),
      confirm: confirmSchema
    }
  },
  {
    name: "codesys_delete_object",
    title: "Delete CODESYS Object",
    description:
      "Delete an object (POU, folder, ...) from the project tree. Destructive and irreversible: requires " +
      "explicit user approval and confirm=true. " + BRIDGE_NOTE,
    op: "delete_object",
    schema: { project: p, path: obj, confirm: confirmSchema }
  },
  // ---------- library manager ----------
  {
    name: "codesys_validate_library",
    title: "Validate CODESYS Library",
    description:
      "Standalone pool-check of a .library: no mirror project needed, returns error/warning counts in ~7 s. " +
      BRIDGE_NOTE,
    op: "validate_library",
    schema: { library: lib }
  },
  {
    name: "codesys_lib_install",
    title: "Install CODESYS Library to Repository",
    description: "Install a .library into a library repository so projects can reference it.",
    op: "lib_install",
    schema: {
      library: lib,
      repo: z.string().optional().describe("Repository name; defaults to the first repository."),
      overwrite: z.boolean().optional().describe("Overwrite an existing installation.")
    }
  },
  {
    name: "codesys_lib_add",
    title: "Add Library Reference to Project",
    description:
      "Add a library reference to the project's Library Manager (objects are used via the library namespace, " +
      "not copied into the project). " + BRIDGE_NOTE,
    op: "lib_add",
    schema: {
      project: p,
      library: z.string().min(1).describe("Library display name or name prefix, e.g. 'MyLib'."),
      application: appPath.optional().describe("Defaults to the project's active application."),
      namespace: z.string().optional().describe("Namespace override, e.g. 'Kimi_Lib'.")
    }
  },
  {
    name: "codesys_lib_remove",
    title: "Remove Library Reference",
    description: "Remove a library reference from the project's Library Manager. " + BRIDGE_NOTE,
    op: "lib_remove",
    schema: {
      project: p,
      library: z.string().min(1),
      application: appPath.optional()
    }
  },
  {
    name: "codesys_lib_list",
    title: "List Project Library References",
    description: "List libraries referenced by the project's Library Manager. " + BRIDGE_NOTE,
    op: "lib_list",
    schema: { project: p, application: appPath.optional() }
  },
  // ---------- external files / native objects ----------
  {
    name: "codesys_add_file",
    title: "Attach External File to Project",
    description:
      "Attach an external file (e.g. Markdown documentation) to the project root, a device or an application. " +
      "mode 'link' keeps the file on disk; 'embed' stores it inside the project. " + BRIDGE_NOTE,
    op: "add_file",
    schema: {
      project: p,
      file: z.string().min(1).describe("Absolute path to the file on disk."),
      path: obj.optional().describe("Target container path; omit for the project root."),
      name: z.string().optional().describe("Object name in the project tree."),
      mode: z.enum(["link", "embed", "link_and_embed"]).optional()
    }
  },
  {
    name: "codesys_native_export",
    title: "Export Native (Non-Textual) Object",
    description:
      "Export a non-textual object (Alarm Configuration, AlarmGroup, AlarmGroupTemplate, UnitConversion, ...) " +
      "to its native XML representation for inspection or editing. " + BRIDGE_NOTE,
    op: "native_export",
    schema: { project: p, path: obj, file: z.string().min(1).describe("Absolute output .xml path.") }
  },
  {
    name: "codesys_native_import",
    title: "Import Native (Non-Textual) Object",
    description:
      "Import a native XML file. NOTE: import ADDS a copy next to existing objects — check for duplicates " +
      "afterwards (export -> edit -> delete old -> import is the safe update cycle). " + BRIDGE_NOTE,
    op: "native_import",
    schema: {
      project: p,
      file: z.string().min(1).describe("Absolute path to the native XML file."),
      parent: obj.optional().describe("Target container; omit for the project root.")
    }
  },
  // ---------- online cycle ----------
  {
    name: "codesys_online_login",
    title: "Online Login",
    description:
      "Login to the device's application on a running PLC runtime. Downloads the application if the runtime " +
      "differs from the project. The session is cached for subsequent online calls. Requires the CODESYS " +
      "Gateway and a running runtime (e.g. 'CODESYS Control Win V3' service). " + BRIDGE_NOTE,
    op: "online_login",
    schema: {
      project: p,
      application: appPath,
      keep_primary: z.boolean().optional().describe("Keep the project primary while the session lives.")
    }
  },
  {
    name: "codesys_online_logout",
    title: "Online Logout",
    description: "Logout and dispose a cached online session.",
    op: "online_logout",
    schema: { project: p, application: appPath }
  },
  {
    name: "codesys_online_status",
    title: "Online Sessions Status",
    description: "List cached online sessions with login/application/operation state and forced expressions.",
    op: "online_status",
    schema: {}
  },
  {
    name: "codesys_online_read",
    title: "Read Online Values",
    description: "Read current values of expressions from the logged-in application (monitoring). " + BRIDGE_NOTE,
    op: "online_read",
    schema: {
      project: p,
      application: appPath,
      expressions: z.array(z.string().min(1)).min(1).describe("e.g. ['PRG_Doser.rTarget', 'PRG_Doser.xReady']")
    }
  },
  {
    name: "codesys_online_write",
    title: "Write Online Values",
    description:
      "Write values to variables of the logged-in application (one-shot write; values are overwritten by the " +
      "next PLC cycle). For I/O simulation use codesys_online_force. " + BRIDGE_NOTE,
    op: "online_write",
    schema: {
      project: p,
      application: appPath,
      values: z.record(z.string()).describe('e.g. {"PRG_Doser.rTarget": "7.5"}')
    }
  },
  {
    name: "codesys_online_force",
    title: "Force Online Values",
    description:
      "Force variables of the logged-in application (values stick until unforced). Destructive on real " +
      "equipment — use with explicit user approval. " + BRIDGE_NOTE,
    op: "online_force",
    schema: {
      project: p,
      application: appPath,
      values: z.record(z.string())
    }
  },
  {
    name: "codesys_online_unforce",
    title: "Unforce Online Values",
    description: "Unforce given expressions, or all forced values when expressions are omitted. " + BRIDGE_NOTE,
    op: "online_unforce",
    schema: {
      project: p,
      application: appPath,
      expressions: z.array(z.string().min(1)).optional()
    }
  }
];

export function registerBridgeTools(server: McpServer): void {
  for (const spec of BRIDGE_TOOLS) {
    server.registerTool(
      spec.name,
      {
        title: spec.title,
        description: spec.description,
        inputSchema: spec.schema
      },
      async (args) => runTask(spec.op, args as Record<string, unknown>)
    );
  }
}
