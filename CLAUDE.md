# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MaxMSP-MCP-Server bridges LLMs and Max/MSP via the Model Context Protocol (MCP). It lets LLMs read, explain, modify, and create objects within live Max patches. This is a third-party project, not made by Cycling '74.

## Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
python install.py --client claude   # or --client cursor / --client vscode
```

Requires Python 3.8+, uv, and Max 9+ (V8 JS engine required).

In Max: open `MaxMSP_Agent/demo.maxpat`, run `script npm install` in the first tab, then `script start` in the second tab.

## Running the Server

```bash
mcp run server.py
```

The MCP server connects to Max via Socket.IO on `http://127.0.0.1:5002` (namespace `/mcp`). These are configurable via env vars `SOCKETIO_SERVER_URL`, `SOCKETIO_SERVER_PORT`, `NAMESPACE`.

## Architecture

The system has three layers connected via Socket.IO:

1. **MCP Server** (`server.py`) — Python FastMCP server exposing tools to LLM clients. Connects as a Socket.IO *client* to the Node.js relay. Loads `docs.json` (6MB flattened Max object documentation) at startup for `list_all_objects` and `get_object_doc` tools.

2. **Node.js Relay** (`MaxMSP_Agent/max_mcp_node.js`) — Socket.IO server running inside Max's `node.script` object on port 5002. Bridges between the Python MCP server and Max's JS environment by routing `command` events (one-way actions) and `request`/`response` events (round-trip queries).

3. **Max JS Scripts** (`MaxMSP_Agent/max_mcp.js`, `max_mcp_v8_add_on.js`) — Run inside Max's `js` objects, directly manipulate the patcher via `this.patcher` API. `max_mcp.js` handles all patch operations (add/remove/connect objects, get state). `max_mcp_v8_add_on.js` enriches patcher data with `boxtext` (requires V8 engine).

### Communication Flow

- **Commands** (fire-and-forget): MCP server → Socket.IO `command` event → Node relay → Max JS → patcher manipulation
- **Requests** (round-trip with response): MCP server emits `request` with a UUID, Node relay forwards to Max JS, Max JS collects data and emits `response` back with matching `request_id`. Python side awaits the future with a 2-second timeout.

### MCP Tools

Three categories:
- **Action tools**: `add_max_object`, `remove_max_object`, `connect_max_objects`, `disconnect_max_objects`, `set_object_attribute`, `set_message_text`, `send_bang_to_object`, `send_messages_to_object`, `set_number` — send commands, no return value
- **Query tools**: `get_objects_in_patch`, `get_objects_in_selected`, `get_object_attributes`, `get_avoid_rect_position`, `list_all_objects`, `get_object_doc` — return patch state or documentation
- **Target tools**: `set_target_to_front_patcher`, `set_target_to_agent_patcher`, `get_target_patcher_info` — switch which patcher subsequent operations act on

### Patcher Targeting

By default all operations target the patcher containing the agent UI (`this.patcher` in `max_mcp.js`). To work on a different patch, the LLM calls `set_target_to_front_patcher` (which captures `max.frontpatcher` at that moment); operations then run on the captured patcher until it is reset via `set_target_to_agent_patcher`. The target is stored in the `target_override` module-level variable and resolved via `get_target()` on every operation.

### Key Conventions

- Objects in Max patches are identified by `varname` (scripting name). Objects prefixed `maxmcpid` are internal to the MCP agent UI and are filtered out of queries.
- `docs.json` is a flat JSON file keyed by Max object category, with each entry containing name, description, inlets, outlets, arguments, methods, and attributes.
- `install.py` writes MCP server config into the client's config file (Claude Desktop, Claude Code, Cursor, or VS Code) pointing to the local `.venv`. Claude Code writes to `.mcp.json` in the project root.
- Request timeout for round-trip queries is configurable via `SOCKETIO_TIMEOUT` env var (default 2.0s).
