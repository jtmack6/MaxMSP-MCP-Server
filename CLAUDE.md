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

Four categories:
- **Action tools**: `add_max_object`, `remove_max_object`, `connect_max_objects`, `disconnect_max_objects`, `set_object_attribute`, `set_message_text`, `send_bang_to_object`, `send_messages_to_object`, `set_number` — send commands, no return value
- **Query tools**: `get_objects_in_patch`, `get_objects_in_selected`, `get_object_attributes`, `get_avoid_rect_position`, `list_all_objects`, `get_object_doc` — return patch state or documentation
- **Target tools**: `set_target_to_front_patcher`, `set_target_to_agent_patcher`, `get_target_patcher_info` — switch which patcher subsequent operations act on
- **RAG tool** (optional): `query_max_docs` — semantic search over an Open WebUI knowledge base for the Max 9 User Reference. Only registered when `OPENWEBUI_URL`, `OPENWEBUI_API_KEY`, and `OPENWEBUI_MAX_COLLECTION_ID` env vars are all set. Calls `POST /api/v1/retrieval/query/collection` and returns top-k chunks with source filenames and similarity scores.

### Patcher Targeting

By default all operations target the patcher containing the agent UI (`this.patcher` in `max_mcp.js`). To work on a different patch, the LLM calls `set_target_to_front_patcher` (which captures `max.frontpatcher` at that moment); operations then run on the captured patcher until it is reset via `set_target_to_agent_patcher`. The target is stored in the `target_override` module-level variable and resolved via `get_target()` on every operation.

### Embedded Agent (optional)

The server can also run a full Anthropic agent loop *inside itself*, so Max can be its own chat client — prompts come from a `[textedit]` in Max, responses stream back to a `[message]` or `[comment]` display. The same `@mcp.tool()`-decorated functions are reused for both the external MCP interface and the embedded agent, so there's one source of truth.

**Enabling:** set `ANTHROPIC_API_KEY` (and optionally `AGENT_MODEL`, default `claude-opus-4-7`) in the MCP server env. If the key is missing, the `prompt` event returns an error status and no external MCP functionality is affected.

**Flow:**
```
Max [textedit] -> message "prompt <text>" -> node.script
                     |
                     v Socket.IO event "prompt"
                  Python (run_agent_loop)
                     |
     streams text deltas as "agent_text" events
                     |
                     v
           node.script outlet "agent_text" -> Max display
```

**Socket.IO events (Python <-> Max):**
- `prompt` (Max → Python): `{text: "..."}` — the user's request
- `agent_text` (Python → Max): `{text: "..."}` — streamed text delta from Claude
- `agent_status` (Python → Max): `{status: "thinking"|"done"|"error", ...}`
- `agent_tool_use` (Python → Max): `{name: "...", input: {...}}` — fired when Claude calls any MCP tool

When Claude uses a tool (e.g. `add_max_object`), the existing Socket.IO `command`/`request` flow kicks in, so the patch edits happen without any additional plumbing. The tool functions run in the Python process and emit commands back to Max exactly as they do when called from an external MCP client.

**Max-side wiring (minimal):**
```
[textedit]                                        // user types here
  |
  [prepend prompt]                                // prefix so node.script recognizes the message
  |
  [node.script max_mcp_node.js]                   // same node.script as the MCP relay
  |
  [route agent_text agent_status agent_tool_use]  // split the outlet by tag
  |  |  |
  |  |  [message] / display status
  |  [prepend set]
  |  |
  [message] / [comment] / [textedit]              // display streamed text
```

The agent loop lives at `server.py:run_agent_loop`. Max JS side needs no changes — everything goes through the existing node.script relay.

### Key Conventions

- Objects in Max patches are identified by `varname` (scripting name). Objects prefixed `maxmcpid` are internal to the MCP agent UI and are filtered out of queries.
- `docs.json` is a flat JSON file keyed by Max object category, with each entry containing name, description, inlets, outlets, arguments, methods, and attributes.
- `install.py` writes MCP server config into the client's config file (Claude Desktop, Claude Code, Cursor, or VS Code) pointing to the local `.venv`. Claude Code writes to `.mcp.json` in the project root.
- Request timeout for round-trip queries is configurable via `SOCKETIO_TIMEOUT` env var (default 2.0s).

## Generative Max Workflow

When the user is building a Max patch collaboratively (not just debugging this codebase), follow this process:

### Session start

1. Ask the user to save their working patch to disk if it isn't already (untitled patchers can't be targeted).
2. Call `watch_for_target_patcher` (30s timeout is a good default) and instruct the user to click inside the target patcher in Max. `max.frontpatcher` is only valid while Max has OS focus, which is why polling from inside Max is required.
3. Confirm capture via `get_target_patcher_info` — `is_agent` should be `false` and the title should match.

### Choose objects via RAG first

Before building anything non-trivial, query `query_max_docs` to see how the Max manual recommends doing it. Do NOT rely on training-data knowledge of Max — the manual has specifics (inlet bindings, multichannel wrappers, signal-rate vs. message-rate distinctions) that are easy to get wrong.

- "How do I X?" → `query_max_docs` (semantic search over the manual)
- "What exactly does object Y do?" → `get_object_doc` (exact reference page)
- Choosing between similar objects (e.g. `gate~` vs. `selector~`, `matrix~` vs. `mc.matrix~`) → `query_max_docs` to compare
- Understanding unfamiliar concepts (MC wrapper, gen~, poly~, jit matrices) → `query_max_docs`

Cite the source filename when referencing manual content so the user can dig in further.

### Audio-output scaffold

For patches that make sound, build this scaffold first so the user can toggle `ezdac~` once and hear every subsequent change live:

```
<generative stuff> -> [gain *~ 0.2] -> [ezdac~ (left & right)]
```

Varnames: `gain` for the `*~`, `out` for the `ezdac~`. Place them at the bottom of the patch so the generative area can grow upward.

### Varname conventions

Use semantic varnames so later tool calls can reference parts of the graph without re-querying. Prefer these defaults:

- Oscillators: `osc1`, `osc2`, ...
- LFOs: `lfo1`, `lfo2`, ... (usually `cycle~` at sub-audio rate or `phasor~`)
- Filters: `filter1`, `filter2`, ... (specify type in comment if not obvious)
- Envelopes: `env1`, ...
- Effects: `rev` (reverb), `del` (delay), `dist` (distortion), `chorus`
- Control: `gain`, `out`, `master_level`
- UI inputs: `<param>_ctl` (e.g. `phase_ctl`, `freq_ctl`, `cutoff_ctl`)

### Iteration loop

1. User describes desired behavior in natural language ("warmer", "slow LFO on cutoff", "second voice detuned up a fifth").
2. Query RAG for the right object(s) if the choice isn't obvious.
3. Propose the plan in one or two sentences *before* building, so the user can redirect.
4. Build: `add_max_object` for each new piece, then `connect_max_objects` for each wire. Connect into the existing named graph where possible (e.g. new voice → existing `gain`).
5. Tell the user to click `ezdac~` (if off) and listen, or adjust a specific flonum/slider.

### Destructive operations

`remove_max_object` deletes the box and its wires but can't be undone via MCP. Before deleting more than one object or rewiring something the user likes, suggest they Cmd+S first so Max's save history holds a checkpoint. Prefer additive changes (new objects, new wires) over destructive ones.

### Position & layout

- Call `get_avoid_rect_position` at session start in the agent patch, skip it in user patches (returns empty when no `maxmcpid`-prefixed objects exist).
- Space objects ~50-70 px vertically and ~80-120 px horizontally.
- Put controls (flonums, sliders) above their target object, so signal flow reads top-to-bottom.
- Audio output (`gain`, `ezdac~`) at the bottom.
