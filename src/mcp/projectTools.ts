import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { runBridgeTaskSerial } from "./bridge.js";

const projectPathSchema = z
  .string()
  .min(1)
  .describe("Absolute path to the .project file, e.g. D:/CodeSys Project/MyProject.project");

const objectPathSchema = z
  .string()
  .min(1)
  .describe("Object path inside the project tree, e.g. 'MyFolder/FB_Motor'. Use the POU/folder names as shown in the CODESYS tree.");

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

export function registerProjectTools(server: McpServer): void {
  server.registerTool(
    "codesys_project_open",
    {
      title: "Open CODESYS Project",
      description:
        "Open a CODESYS project in the headless bridge and return basic info. " + BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema
      }
    },
    async ({ project }) => runTask("open_project", { path: project })
  );

  server.registerTool(
    "codesys_project_info",
    {
      title: "CODESYS Project Info",
      description: "Return path, dirty state and active application of the project. " + BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema
      }
    },
    async ({ project }) => runTask("project_info", { project })
  );

  server.registerTool(
    "codesys_list_objects",
    {
      title: "List CODESYS Project Objects",
      description:
        "List objects (POUs, folders, DUTs, GVLs, devices) in the project tree. " + BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema,
        path: objectPathSchema
          .optional()
          .describe("Optional sub-path to list from. Omit to list from the project root."),
        recursive: z
          .boolean()
          .optional()
          .describe("Recurse into sub-folders. Defaults to true."),
        max_depth: z
          .number()
          .int()
          .min(1)
          .optional()
          .describe("Optional maximum recursion depth."),
        slim: z
          .boolean()
          .optional()
          .describe(
            "Token diet: return only name+type per object (no guid/parent/folder flags). Defaults to false."
          )
      }
    },
    async ({ project, path, recursive, max_depth, slim }) =>
      runTask("list_objects", {
        project,
        ...(path !== undefined ? { path } : {}),
        ...(recursive !== undefined ? { recursive } : {}),
        ...(max_depth !== undefined ? { max_depth } : {}),
        ...(slim !== undefined ? { slim } : {})
      })
  );

  server.registerTool(
    "codesys_get_object",
    {
      title: "Get CODESYS Object Source",
      description:
        "Get the declaration and implementation text of a POU, DUT, GVL or action, plus its children. " +
        BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema,
        path: objectPathSchema,
        slim: z
          .boolean()
          .optional()
          .describe(
            "Token diet: list children as plain names instead of full object info. Defaults to false."
          )
      }
    },
    async ({ project, path, slim }) =>
      runTask("get_object", { project, path, ...(slim !== undefined ? { slim } : {}) })
  );

  server.registerTool(
    "codesys_create_pou",
    {
      title: "Create CODESYS POU",
      description:
        "Create a POU (program / function block / function) in the project, optionally with ST declaration and implementation text. " +
        BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema,
        name: z.string().min(1).describe("Name of the new POU (IEC identifier)."),
        pou_type: z
          .enum(["program", "function_block", "function"])
          .describe("Kind of POU. Use 'function' only with return_type."),
        folder: objectPathSchema
          .optional()
          .describe("Optional target folder path; omit to create at the project root."),
        return_type: z
          .string()
          .optional()
          .describe("Return type, required for pou_type 'function' (e.g. 'INT', 'BOOL')."),
        base_type: z
          .string()
          .optional()
          .describe("Base function block to EXTENDS (function_block only)."),
        interfaces: z
          .string()
          .optional()
          .describe("Comma-separated interfaces to IMPLEMENTS (function_block only)."),
        declaration: z
          .string()
          .optional()
          .describe(
            "Full ST declaration text including the header line, e.g. \"FUNCTION_BLOCK FB_Motor\\nVAR_INPUT\\n...\\nEND_VAR\"."
          ),
        implementation: z
          .string()
          .optional()
          .describe("ST implementation body text (statements only, no header).")
      }
    },
    async ({ project, name, pou_type, folder, return_type, base_type, interfaces, declaration, implementation }) =>
      runTask("create_pou", {
        project,
        name,
        pou_type,
        ...(folder !== undefined ? { folder } : {}),
        ...(return_type !== undefined ? { return_type } : {}),
        ...(base_type !== undefined ? { base_type } : {}),
        ...(interfaces !== undefined ? { interfaces } : {}),
        ...(declaration !== undefined ? { declaration } : {}),
        ...(implementation !== undefined ? { implementation } : {})
      })
  );

  server.registerTool(
    "codesys_create_member",
    {
      title: "Create CODESYS POU Member",
      description:
        "Create an action, method or property on an existing POU, optionally with ST implementation text. " +
        BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema,
        path: objectPathSchema.describe("Path of the parent POU, e.g. 'FB_Motor'."),
        kind: z.enum(["action", "method", "property"]),
        name: z.string().min(1).describe("Name of the new member."),
        return_type: z
          .string()
          .optional()
          .describe("Return type for method/property (property defaults to INT)."),
        implementation: z.string().optional().describe("ST body text of the member.")
      }
    },
    async ({ project, path, kind, name, return_type, implementation }) =>
      runTask("create_member", {
        project,
        path,
        kind,
        name,
        ...(return_type !== undefined ? { return_type } : {}),
        ...(implementation !== undefined ? { implementation } : {})
      })
  );

  server.registerTool(
    "codesys_create_dut",
    {
      title: "Create CODESYS DUT",
      description:
        "Create a data unit type (structure, enumeration, alias, union), optionally with declaration text. " +
        BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema,
        name: z.string().min(1),
        dut_type: z.enum(["structure", "enumeration", "alias", "union"]).optional(),
        folder: objectPathSchema.optional(),
        base_type: z.string().optional().describe("Base type for alias DUTs."),
        declaration: z.string().optional().describe("Full ST declaration text of the DUT.")
      }
    },
    async ({ project, name, dut_type, folder, base_type, declaration }) =>
      runTask("create_dut", {
        project,
        name,
        ...(dut_type !== undefined ? { dut_type } : {}),
        ...(folder !== undefined ? { folder } : {}),
        ...(base_type !== undefined ? { base_type } : {}),
        ...(declaration !== undefined ? { declaration } : {})
      })
  );

  server.registerTool(
    "codesys_create_gvl",
    {
      title: "Create CODESYS GVL",
      description:
        "Create a global variable list, optionally with declaration text. " + BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema,
        name: z.string().min(1),
        folder: objectPathSchema.optional(),
        declaration: z.string().optional().describe("VAR_GLOBAL ... END_VAR text.")
      }
    },
    async ({ project, name, folder, declaration }) =>
      runTask("create_gvl", {
        project,
        name,
        ...(folder !== undefined ? { folder } : {}),
        ...(declaration !== undefined ? { declaration } : {})
      })
  );

  server.registerTool(
    "codesys_set_code",
    {
      title: "Set CODESYS Object Code",
      description:
        "Replace the declaration or implementation text of an existing object with new ST text. " +
        BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema,
        path: objectPathSchema,
        part: z
          .enum(["declaration", "implementation"])
          .describe("Which text part to replace."),
        text: z.string().describe("New full text for the selected part.")
      }
    },
    async ({ project, path, part, text }) =>
      runTask("set_code", { project, path, part, text })
  );

  server.registerTool(
    "codesys_save_project",
    {
      title: "Save CODESYS Project",
      description: "Save the project to disk. " + BRIDGE_NOTE,
      inputSchema: {
        project: projectPathSchema
      }
    },
    async ({ project }) => runTask("save_project", { project })
  );
}
