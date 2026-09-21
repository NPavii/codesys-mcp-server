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
  },
  // ---------- project lifecycle / multi-application ----------
  {
    name: "codesys_create_project",
    title: "Create CODESYS Project",
    description: "Create a new empty project on disk (saved immediately). " + BRIDGE_NOTE,
    op: "create_project",
    schema: {
      name: z.string().min(1).describe("Project name."),
      path: z.string().min(1).describe("Absolute output path for the .project file.")
    }
  },
  {
    name: "codesys_list_applications",
    title: "List Applications",
    description: "List every application in the project with its hosting device and active flag. " + BRIDGE_NOTE,
    op: "list_applications",
    schema: { project: p }
  },
  {
    name: "codesys_set_active_application",
    title: "Set Active Application",
    description: "Mark an application as the project's active application. " + BRIDGE_NOTE,
    op: "set_active_application",
    schema: { project: p, application: appPath }
  },
  {
    name: "codesys_save_project_as",
    title: "Save Project As",
    description:
      "Save the project under a new file path (the warm session retargets to the new file). " + BRIDGE_NOTE,
    op: "save_project_as",
    schema: {
      project: p,
      file: z.string().min(1).describe("Absolute target .project path."),
      password: z.string().optional().describe("Optional project password.")
    }
  },
  {
    name: "codesys_save_project_archive",
    title: "Save Project Archive",
    description: "Pack the whole project (with all objects and references) into a .zip archive. " + BRIDGE_NOTE,
    op: "save_project_archive",
    schema: {
      project: p,
      file: z.string().min(1).describe("Absolute output .zip path."),
      comment: z.string().optional().describe("Archive comment.")
    }
  },
  {
    name: "codesys_clean_all",
    title: "Clean Build Artifacts",
    description: "Remove all compiled code / build artifacts of the project (project-level clean). " + BRIDGE_NOTE,
    op: "clean_all",
    schema: { project: p }
  },
  {
    name: "codesys_get_compiler_version",
    title: "Get Compiler Version",
    description: "Read the project's compiler version string (not available on all SPs). " + BRIDGE_NOTE,
    op: "get_compiler_version",
    schema: { project: p }
  },
  {
    name: "codesys_set_compiler_version_newest",
    title: "Set Compiler Version to Newest",
    description: "Switch the project to the newest installed compiler version. " + BRIDGE_NOTE,
    op: "set_compiler_version_newest",
    schema: { project: p }
  },
  {
    name: "codesys_list_open_projects",
    title: "List Open Projects (warm daemon)",
    description: "List the projects currently open in the warm ScriptEngine session.",
    op: "list_open_projects",
    schema: {}
  },
  // ---------- object tree ops ----------
  {
    name: "codesys_rename_object",
    title: "Rename Object",
    description:
      "Rename an object in the project tree. NOTE on SP21: the rename does not rewrite the " +
      "declaration header inside POU text (update_references only fixes references) - fix the " +
      "header text afterwards if the object is a POU. " + BRIDGE_NOTE,
    op: "rename_object",
    schema: {
      project: p,
      path: obj,
      new_name: z.string().min(1),
      update_references: z.boolean().optional().describe("Update references to the object. Defaults to true.")
    }
  },
  {
    name: "codesys_move_object",
    title: "Move Object",
    description:
      "Move an object to another parent container. Use index -1 (append) unless the target node " +
      "has sorted children. " + BRIDGE_NOTE,
    op: "move_object",
    schema: {
      project: p,
      path: obj,
      new_parent: obj.describe("Target container path; omit (empty) for the project root."),
      index: z.number().int().optional().describe("Insert position; -1 = append (default).")
    }
  },
  {
    name: "codesys_dump_pou_code",
    title: "Bulk-Dump POU Code",
    description:
      "Return declaration+implementation of every textual object under a root (default: whole " +
      "project) in one call - much cheaper than reading objects one by one. " + BRIDGE_NOTE,
    op: "dump_pou_code",
    schema: {
      project: p,
      root: obj.optional().describe("Root container path; omit for the whole project."),
      max_objects: z.number().int().optional().describe("Safety cap on returned objects (default 2000).")
    }
  },
  {
    name: "codesys_set_exclude_from_build",
    title: "Exclude Object From Build",
    description: "Include/exclude an object from the build (excluded objects are not compiled). " + BRIDGE_NOTE,
    op: "set_exclude_from_build",
    schema: { project: p, path: obj, exclude: z.boolean() }
  },
  {
    name: "codesys_signature_crc",
    title: "Read Build Signature/CRC",
    description:
      "Read the compile signature/CRC of an application or POU (requires a compile context from a " +
      "previous build; may be unavailable on some SPs). " + BRIDGE_NOTE,
    op: "signature_crc",
    schema: { project: p, path: obj }
  },
  {
    name: "codesys_grant_object_access",
    title: "Grant Object Access (user management)",
    description: "Grant object-level access permissions to a user or group (project user management). " + BRIDGE_NOTE,
    op: "grant_object_access",
    schema: {
      project: p,
      path: obj,
      group: z.string().describe("User or group name."),
      permissions: z.string().describe("Permission flags, e.g. 'RW'."),
      state: z.string().optional().describe("Access state, e.g. 'allow'.")
    }
  },
  // ---------- PLCopenXML exchange ----------
  {
    name: "codesys_export_plcopen_xml",
    title: "Export to PLCopen XML",
    description: "Export an object subtree (or the whole project) to a PLCopen XML file. " + BRIDGE_NOTE,
    op: "export_plcopen_xml",
    schema: {
      project: p,
      path: obj.optional().describe("Object path; omit to export the whole project."),
      file: z.string().min(1).describe("Absolute output .xml path."),
      recursive: z.boolean().optional().describe("Include children (default true).")
    }
  },
  {
    name: "codesys_import_plcopen_xml",
    title: "Import PLCopen XML",
    description:
      "Import objects from a PLCopen XML file. Objects are ADDED next to existing ones - rename " +
      "them inside the XML first if a name collision would occur. " + BRIDGE_NOTE,
    op: "import_plcopen_xml",
    schema: {
      project: p,
      file: z.string().min(1).describe("Absolute path to the PLCopen XML file."),
      import_folder_structure: z.boolean().optional().describe("Recreate folder structure (default true).")
    }
  },
  // ---------- task configuration ----------
  {
    name: "codesys_list_tasks",
    title: "List Tasks",
    description: "List the tasks of the application's Task Configuration with kind, interval, priority and POU calls. " + BRIDGE_NOTE,
    op: "list_tasks",
    schema: { project: p, application: appPath.optional() }
  },
  {
    name: "codesys_configure_task",
    title: "Create/Configure Task",
    description:
      "Create or reconfigure a cyclic/event task: kind, interval, priority, event, add a POU call. " +
      "NOTE on SP21 Patch 1: remove_pou is NOT supported by the ScriptEngine (reported as an honest " +
      "error) - remove POU calls in the CODESYS GUI. " + BRIDGE_NOTE,
    op: "configure_task",
    schema: {
      project: p,
      application: appPath.optional(),
      task: z.string().min(1).describe("Task name."),
      create: z.boolean().optional().describe("Create the task if missing (default true)."),
      kind: z.string().optional().describe("Cyclic | Event | ..."),
      interval: z.string().optional().describe("e.g. 'T#20ms'"),
      interval_unit: z.string().optional(),
      priority: z.number().int().optional(),
      event: z.string().optional().describe("Event name (event tasks)."),
      pou: z.string().optional().describe("POU name to add to the task calls."),
      remove_pou: z.string().optional().describe("POU call to remove (SP21: not supported, honest error).")
    }
  },
  // ---------- device / parameters ----------
  {
    name: "codesys_add_device",
    title: "Add Device",
    description: "Add a device (or module below a device) to the project tree, e.g. a CODESYS Control Win PLC. " + BRIDGE_NOTE,
    op: "add_device",
    schema: {
      project: p,
      device_name: z.string().min(1).describe("Device type name, e.g. 'CODESYS Control Win V3 x64'."),
      version: z.string().optional().describe("Device version, e.g. '3.5.15.0'."),
      name: z.string().optional().describe("Instance name in the tree."),
      module: obj.optional().describe("Parent device path to add a module below.")
    }
  },
  {
    name: "codesys_device_info",
    title: "Device Info",
    description: "Read device identity (name, type, id, version, gateway, address). " + BRIDGE_NOTE,
    op: "device_info",
    schema: { project: p, device: obj.optional() }
  },
  {
    name: "codesys_list_device_parameters",
    title: "List Device Parameters",
    description: "List the device parameters / connectors of a device. " + BRIDGE_NOTE,
    op: "list_device_parameters",
    schema: { project: p, device: obj.optional() }
  },
  {
    name: "codesys_set_device_parameter",
    title: "Set Device Parameter",
    description: "Set a single device parameter value. " + BRIDGE_NOTE,
    op: "set_device_parameter",
    schema: {
      project: p,
      device: obj.optional(),
      parameter: z.string().min(1).describe("Parameter name."),
      value: z.string().describe("New value (string).")
    }
  },
  {
    name: "codesys_set_device_state",
    title: "Set Device State",
    description: "Switch the device state, e.g. simulation_on / simulation_off (SP21: not supported on real targets). " + BRIDGE_NOTE,
    op: "set_device_state",
    schema: {
      project: p,
      device: obj.optional(),
      action: z.string().min(1).describe("e.g. 'simulation_on', 'simulation_off'.")
    }
  },
  {
    name: "codesys_device_reachable",
    title: "Check Device Reachable",
    description: "Pre-flight: is the device's cached address visible via the gateway scan? Honest error when no gateway is configured. " + BRIDGE_NOTE,
    op: "device_reachable",
    schema: { project: p, device: obj.optional() }
  },
  {
    name: "codesys_device_rebind",
    title: "Rebind Device Address",
    description: "Rebind the device to a network address matched by name/address/device-id. " + BRIDGE_NOTE,
    op: "device_rebind",
    schema: {
      project: p,
      device: obj.optional(),
      match_name: z.string().optional(),
      match_address: z.string().optional(),
      match_device_id: z.string().optional()
    }
  },
  {
    name: "codesys_device_user_add",
    title: "Add Device User",
    description: "Add a user to the project's device user management. " + BRIDGE_NOTE,
    op: "device_user_add",
    schema: {
      project: p,
      device: obj.optional(),
      user: z.string().min(1),
      password: z.string().min(1),
      can_change_password: z.boolean().optional(),
      must_change_password: z.boolean().optional()
    }
  },
  {
    name: "codesys_scan_network",
    title: "Scan Network",
    description:
      "Perform (or read the cached) gateway network scan and list reachable targets. Missing gateway " +
      "is reported as an honest result, not an exception. " + BRIDGE_NOTE,
    op: "scan_network",
    schema: {
      project: p,
      device: obj.optional(),
      use_cache: z.boolean().optional().describe("Use the gateway's cached scan result first.")
    }
  },
  {
    name: "codesys_io_mappings_csv",
    title: "IO Mappings CSV",
    description: "Export/import device IO mappings as CSV (needs scripting API 3.5.8.0+). " + BRIDGE_NOTE,
    op: "io_mappings_csv",
    schema: {
      project: p,
      device: obj.optional().describe("Device path; defaults to the active application's device."),
      direction: z.enum(["export", "import"]).describe("'export' writes a CSV; 'import' reads it back."),
      file: z.string().min(1).describe("Absolute CSV file path.")
    }
  },
  // ---------- application build actions ----------
  {
    name: "codesys_application_build_action",
    title: "Application Build Action",
    description: "Run generate_code / rebuild / clean on an application (no full message dump; use codesys_build for that). " + BRIDGE_NOTE,
    op: "application_build_action",
    schema: {
      project: p,
      application: appPath.optional(),
      action: z.enum(["generate_code", "rebuild", "clean"]).optional()
    }
  },
  {
    name: "codesys_online_change_check",
    title: "Check Online Change Possible",
    description: "Check whether an online change is possible for the application (requires compile context). " + BRIDGE_NOTE,
    op: "online_change_check",
    schema: { project: p, application: appPath.optional() }
  },
  // ---------- online lifecycle (need login) ----------
  {
    name: "codesys_download_application",
    title: "Download Application",
    description: "Login to the device and download the application (creates a cached online session). " + BRIDGE_NOTE,
    op: "download_application",
    schema: {
      project: p,
      application: appPath,
      login_wait: z.number().int().optional().describe("Seconds to wait for login (default 30).")
    }
  },
  {
    name: "codesys_application_start_stop",
    title: "Start/Stop Application",
    description: "Start or stop the logged-in application on the device. " + BRIDGE_NOTE,
    op: "application_start_stop",
    schema: {
      project: p,
      application: appPath,
      action: z.enum(["start", "stop"])
    }
  },
  {
    name: "codesys_application_state",
    title: "Application State",
    description: "Read the cached online session state (login/application/operation state). " + BRIDGE_NOTE,
    op: "application_state",
    schema: { project: p, application: appPath }
  },
  {
    name: "codesys_application_reset",
    title: "Reset Application",
    description:
      "Warm/cold reset of the logged-in application. 'origin' additionally wipes retain/persistent " +
      "data and requires confirm=true. " + BRIDGE_NOTE,
    op: "application_reset",
    schema: {
      project: p,
      application: appPath,
      level: z.enum(["warm", "cold", "origin"]).optional(),
      confirm: confirmSchema.optional()
    }
  },
  {
    name: "codesys_boot_application_create",
    title: "Create Boot Application",
    description:
      "Create a boot application (offline: writes a .app file, needs generated code; online: writes " +
      "onto the connected device). On SP21 Patch 1 the offline generator may fail with an honest error. " + BRIDGE_NOTE,
    op: "boot_application_create",
    schema: {
      project: p,
      application: appPath.optional(),
      online: z.boolean().optional().describe("Write onto the device (needs online session)."),
      output: z.string().optional().describe("Absolute output .app path (offline mode).")
    }
  },
  {
    name: "codesys_plc_file_list",
    title: "List PLC Files",
    description: "List files/directories in a directory of the PLC filesystem (needs online session). " + BRIDGE_NOTE,
    op: "plc_file_list",
    schema: {
      project: p,
      application: appPath,
      directory: z.string().optional().describe("PLC directory, e.g. ''. Defaults to root.")
    }
  },
  {
    name: "codesys_plc_file_delete",
    title: "Delete PLC File",
    description:
      "Delete a file/directory in the PLC filesystem. Destructive: requires confirm=true. " + BRIDGE_NOTE,
    op: "plc_file_delete",
    schema: {
      project: p,
      application: appPath,
      path: z.string().min(1).describe("PLC file/directory path."),
      is_directory: z.boolean().optional(),
      recursive: z.boolean().optional(),
      confirm: confirmSchema
    }
  },
  {
    name: "codesys_plc_file_transfer",
    title: "Transfer PLC File",
    description: "Upload/download a file between the local disk and the PLC filesystem (needs online session). " + BRIDGE_NOTE,
    op: "plc_file_transfer",
    schema: {
      project: p,
      application: appPath,
      direction: z.enum(["to_plc", "from_plc"]).describe("'to_plc' = download_file (PC->PLC), 'from_plc' = upload_file (PLC->PC)."),
      local: z.string().min(1).describe("Absolute local file path."),
      plc: z.string().min(1).describe("PLC file path."),
      overwrite: z.boolean().optional()
    }
  },
  {
    name: "codesys_source_download",
    title: "Download Project Source to PLC",
    description: "Download the project source archive into the device. " + BRIDGE_NOTE,
    op: "source_download",
    schema: {
      project: p,
      application: appPath,
      compact: z.boolean().optional().describe("Try the compact device-level variant first (falls back to full).")
    }
  },
  {
    name: "codesys_source_upload",
    title: "Upload Project Source from PLC",
    description: "Upload the source archive stored on the device into a local file. " + BRIDGE_NOTE,
    op: "source_upload",
    schema: {
      project: p,
      application: appPath,
      archive: z.string().min(1).describe("Absolute target archive path.")
    }
  },
  // ---------- symbol configuration (OPC UA) ----------
  {
    name: "codesys_symbol_config_create",
    title: "Create Symbol Configuration",
    description: "Create a Symbol Configuration object in the application (for OPC UA / symbol access). " + BRIDGE_NOTE,
    op: "symbol_config_create",
    schema: {
      project: p,
      application: appPath.optional(),
      support_opcua: z.boolean().optional(),
      layout: z.string().optional(),
      export_comments: z.boolean().optional()
    }
  },
  {
    name: "codesys_symbol_config_list",
    title: "List Symbol Configuration",
    description: "List the variables/groups of the application's Symbol Configuration. " + BRIDGE_NOTE,
    op: "symbol_config_list",
    schema: {
      project: p,
      application: appPath.optional(),
      compile: z.boolean().optional(),
      configured_only: z.boolean().optional()
    }
  },
  {
    name: "codesys_symbol_config_set_access",
    title: "Set Symbol Access",
    description: "Set access rights (read/write) for a variable or library in the Symbol Configuration. " + BRIDGE_NOTE,
    op: "symbol_config_set_access",
    schema: {
      project: p,
      application: appPath.optional(),
      variable: z.string().optional().describe("Variable path, e.g. 'PRG_Doser.rTarget'."),
      library_id: z.string().optional().describe("Or: library identifier."),
      access: z.string().optional().describe("e.g. 'R', 'W', 'RW'."),
      signature: z.string().optional()
    }
  },
  {
    name: "codesys_symbol_config_settings_get",
    title: "Get Symbol Config Settings",
    description: "Read the Symbol Configuration settings. " + BRIDGE_NOTE,
    op: "symbol_config_settings_get",
    schema: { project: p, application: appPath.optional() }
  },
  {
    name: "codesys_symbol_config_settings_set",
    title: "Set Symbol Config Settings",
    description: "Update Symbol Configuration settings (layout, filters, flags). " + BRIDGE_NOTE,
    op: "symbol_config_settings_set",
    schema: {
      project: p,
      application: appPath.optional(),
      layout: z.string().optional(),
      content_flags: z.string().optional(),
      direct_io: z.boolean().optional(),
      attr_filter_type: z.string().optional(),
      attr_filter_data: z.string().optional(),
      comment_filter_type: z.string().optional()
    }
  },
  {
    name: "codesys_symbol_config_export_xsd",
    title: "Export Symbol Config XSD",
    description: "Export the Symbol Configuration as XSD (schema of the symbol file). " + BRIDGE_NOTE,
    op: "symbol_config_export_xsd",
    schema: {
      project: p,
      application: appPath.optional(),
      file: z.string().min(1).describe("Absolute output .xsd path.")
    }
  },
  // ---------- NVL (network variables) ----------
  {
    name: "codesys_nvl_sender_set",
    title: "Configure NVL Sender",
    description: "Configure a GVL as an NVL sender (list identifier, port, broadcast, cycle). " + BRIDGE_NOTE,
    op: "nvl_sender_set",
    schema: {
      project: p,
      gvl: obj.describe("Path of the GVL to configure as sender."),
      task: z.string().optional().describe("Task name, e.g. 'MainTask'."),
      list_identifier: z.number().int().optional(),
      port: z.number().int().optional(),
      broadcast_address: z.string().optional(),
      interval: z.string().optional().describe("e.g. 'T#50ms'"),
      min_gap: z.string().optional(),
      cyclic: z.boolean().optional(),
      on_change: z.boolean().optional(),
      pack_variables: z.boolean().optional(),
      checksum: z.boolean().optional(),
      acknowledge: z.boolean().optional()
    }
  },
  {
    name: "codesys_nvl_receiver_create",
    title: "Create NVL Receiver",
    description: "Create an NVL receiver (network variables list binding) in the application. " + BRIDGE_NOTE,
    op: "nvl_receiver_create",
    schema: {
      project: p,
      parent: obj.optional().describe("Target container; default the application."),
      name: z.string().min(1).describe("Receiver object name."),
      sender_gvl: z.string().optional().describe("Binding to the sender GVL."),
      task: z.string().optional()
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
