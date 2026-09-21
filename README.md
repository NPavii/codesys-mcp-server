# CODESYS MCP Server

A Model Context Protocol server for CODESYS Development System V3. It gives MCP clients curated CODESYS guidance, Structured Text writing help, searchable local PDF documentation, and optional live lookup of allowlisted official CODESYS pages.

The server can run over HTTP at `/mcp` or over stdio for direct CLI and agent integration. Official pages are summarized and cited rather than copied into the project. Primary official sources include [CODESYS Online Help](https://content.helpme-codesys.com/) and [CODESYS Examples](https://content.helpme-codesys.com/en/CODESYS%20Examples/_ex_start_page.html).

## Tools

- `search_codesys_docs`: search curated and/or official CODESYS guidance.
- `get_codesys_topic`: explain a known topic such as POUs, function blocks, timers, tasks, variables, libraries, debugging, or Structured Text.
- `codesys_writing_guidance`: provide practical CODESYS and Structured Text design guidance for a requested task.
- `list_codesys_pdfs`: list PDF documents available in the configured PDF folder.
- `search_codesys_pdfs`: search text extracted from local PDF documents.
- `get_codesys_pdf`: return extracted text from a specific local PDF document.
- `get_codesys_page`: fetch the full text content of any allowlisted CODESYS documentation URL.
- `crawl_codesys_help`: crawl and index the CODESYS help site for searchable documentation.

Project automation bridge (works directly on `.project` files via the CODESYS ScriptEngine):

- `codesys_project_open` / `codesys_project_info`: open a project headlessly and inspect basic info.
- `codesys_list_objects` / `codesys_get_object`: list the project tree and read declaration/implementation ST text of any POU, DUT, GVL, or action.
- `codesys_create_pou`: create a PROGRAM / FUNCTION_BLOCK / FUNCTION with optional ST declaration and implementation text.
- `codesys_create_member`: add an action, method, or property to an existing POU.
- `codesys_create_dut` / `codesys_create_gvl`: create data unit types and global variable lists.
- `codesys_set_code`: replace the declaration or implementation text of an existing object.
- `codesys_save_project`: save the project.

### v0.3.0 — project lifecycle and device toolchain (ported from [Codesys-MCP-SP21-plus](https://github.com/phobicdotno/Codesys-MCP-SP21-plus), MIT)

- **Multi-application**: `codesys_list_applications`, `codesys_set_active_application`, `codesys_create_project`, `codesys_list_open_projects`.
- **Object tree**: `codesys_rename_object`, `codesys_move_object`, `codesys_dump_pou_code` (bulk-read all ST code in one call), `codesys_set_exclude_from_build`, `codesys_signature_crc`, `codesys_grant_object_access`.
- **PLCopenXML exchange**: `codesys_export_plcopen_xml`, `codesys_import_plcopen_xml`.
- **Task configuration**: `codesys_list_tasks`, `codesys_configure_task` (create/reconfigure tasks, add POU calls).
- **Devices**: `codesys_add_device`, `codesys_device_info`, `codesys_list_device_parameters`, `codesys_set_device_parameter`, `codesys_set_device_state`, `codesys_device_reachable`, `codesys_device_rebind`, `codesys_device_user_add`, `codesys_scan_network`, `codesys_io_mappings_csv`.
- **Build actions**: `codesys_application_build_action` (generate_code/rebuild/clean), `codesys_online_change_check`, `codesys_get_compiler_version`, `codesys_set_compiler_version_newest`, `codesys_clean_all`.
- **Project files**: `codesys_save_project_as`, `codesys_save_project_archive`.
- **Online lifecycle** (require `codesys_online_login`/`codesys_download_application` and a running PLC runtime): `codesys_application_state`, `codesys_application_start_stop`, `codesys_application_reset`, `codesys_boot_application_create`, `codesys_plc_file_list`, `codesys_plc_file_delete`, `codesys_plc_file_transfer`, `codesys_source_download`, `codesys_source_upload`.
- **Symbol configuration (OPC UA)**: `codesys_symbol_config_create`, `codesys_symbol_config_list`, `codesys_symbol_config_set_access`, `codesys_symbol_config_settings_get` / `_set`, `codesys_symbol_config_export_xsd`.
- **Network variables (NVL)**: `codesys_nvl_sender_set`, `codesys_nvl_receiver_create`.

#### Known SP21 Patch 1 limitations (verified empirically)

- Text writes (`codesys_set_code`, create-with-text) require the project to be the **primary** project in the ScriptEngine session. The bridge promotes/demotes automatically; the warm daemon marks the working project primary via `*` in `bridge/warm_projects.txt`.
- `remove_pou` in `codesys_configure_task` is **not supported**: `pous.remove()` is a silent no-op and task objects cannot be deleted via the API. The op returns an honest error; remove POU calls in the CODESYS GUI.
- Deleting task-tree objects (tasks, Task Configuration) is not possible via the API at all.
- `delete_object` works for objects created in the current warm session; objects loaded from disk may refuse removal (ScriptEngine `remove()` throws NullReference).
- `get_compiler_version` is not available (no such ScriptEngine API on SP21 Patch 1).
- `scan_network` / `device_reachable` need a configured gateway; a missing gateway is reported as an honest result, not an exception.
- The offline `boot_application_create` generator may fail on SP21 Patch 1 even after `generate_code` (reported as an honest error).

### How the project bridge works

Each project tool call writes a task file, starts `CODESYS.exe --noUI --runscript=bridge/bridge.py` (requires the CODESYS ScriptEngine, included with the IDE), executes the operation, saves and closes the project, and returns the result as JSON. Notes:

- The target project must **not** be open in the CODESYS GUI at the same time (file lock).
- Each call takes roughly 10–30 s because a headless CODESYS instance starts per call.
- With the optional warm daemon (`bridge/bridge_daemon.cmd`) the project stays open in a background CODESYS instance and calls take ~1–3 s. The daemon is also what the MCP plugin uses when available.
- Configure via env: `CODESYS_EXE`, `CODESYS_PROFILE`, `CODESYS_BRIDGE_DIR`, `CODESYS_BRIDGE_TIMEOUT_MS` (see `.env.example`).

## What It Does

- Searches curated CODESYS guidance for concepts, syntax, and workflow help.
- Explains common CODESYS topics such as POUs, function blocks, timers, tasks, variables, libraries, debugging, and Structured Text.
- Provides practical Structured Text and project-structure guidance.
- Searches local PDF manuals and notes placed in `docs/pdfs/`.
- Fetches and summarizes allowlisted official CODESYS documentation pages.
- Crawls the official help site to build a searchable index.

## Install For A Human

Use this when you want to run the server yourself on your machine.

Prerequisites:

- Node.js 20 or newer
- `npm`

From a fresh clone:

```bash
npm install
cp .env.example .env
npm run build
```

Start the HTTP server:

```bash
npm start
```

Or start the stdio server:

```bash
npm run start:stdio
```

By default, the HTTP server listens on `http://0.0.0.0:3000/mcp`.

## Install For An LLM Or Agent

> **AI agent?** Read [`agent.md`](agent.md) — it contains the complete, step-by-step self-installation guide written specifically for you. Start there.

Use this section when an LLM is being instructed to connect itself to the server as an MCP client.

Suggested instruction to give the LLM:

```text
Read agent.md in the repository root. It contains the exact steps to install this MCP server into yourself.
```

1. Build the project first:

```bash
npm install
npm run build
```

2. Prefer stdio transport for local agent integration.
3. Add the server to the client using the local build output:

```bash
claude mcp add --transport stdio codesys-docs -- node ./dist/src/cli.js --stdio
```

4. If the client needs HTTP instead, start the server and register the endpoint:

```bash
npm start
```

```bash
claude mcp add --transport http codesys-docs http://localhost:3000/mcp
```

5. If the agent can only work from a published package, use:

```bash
claude mcp add --transport stdio codesys-docs -- npx -y codesys-mcp-server --stdio
```

For Codex CLI:

```bash
codex mcp add codesys-docs -- node ./dist/src/cli.js --stdio
```

## Quick Start

```bash
npm install
cp .env.example .env
npm run build
npm start
```

The server listens on `http://0.0.0.0:3000/mcp` by default.

## Docker

```bash
docker compose up --build
```

The compose file mounts `./docs/pdfs` into the container as read-only, so you can add PDFs locally without rebuilding the image.

## PDF Folder

Put CODESYS manuals, vendor PDFs, project notes, datasheets, or exported documentation in:

```text
docs/pdfs/
```

The MCP server extracts text from `.pdf` files in that folder and makes them available through:

- `search_codesys_docs` with `sourceMode: "pdf"` or `sourceMode: "all"`
- `list_codesys_pdfs`
- `search_codesys_pdfs`
- `get_codesys_pdf`

PDF text extraction is best for searchable text PDFs. Scanned image-only PDFs may return little or no content unless OCR has already been applied. Extracted text may omit diagrams, screenshots, formatting, and some tables, so important engineering details should be verified against the original PDF.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `PORT` | `3000` | HTTP port. |
| `HOST` | `0.0.0.0` | Bind host. |
| `OFFICIAL_LOOKUP_ENABLED` | `true` | Enables live official CODESYS page lookup. |
| `OFFICIAL_LOOKUP_TIMEOUT_MS` | `3500` | Fetch timeout per official page. |
| `OFFICIAL_LOOKUP_CACHE_TTL_SECONDS` | `3600` | In-memory cache TTL for official pages. |
| `PDF_SEARCH_ENABLED` | `true` | Enables text extraction/search for local PDFs. |
| `PDF_DOCS_DIR` | `docs/pdfs` | Folder scanned for `.pdf` files. |
| `PDF_MAX_TEXT_CHARS` | `200000` | Maximum extracted text stored per PDF. |

## CLI Usage

```bash
# HTTP server (default)
node dist/src/cli.js
npm start

# Stdio server (for Claude Code / Codex CLI MCP integration)
node dist/src/cli.js --stdio
npm run start:stdio
```

## Acceptance Checks

```bash
npm test
npm run build
curl http://localhost:3000/healthz
```
