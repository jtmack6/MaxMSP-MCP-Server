# MaxMSP-MCP Server

This project uses the [Model Context Protocol](https://modelcontextprotocol.io/introduction) (MCP) to let LLMs directly understand and generate Max patches.

> **This is a fork** of [`tiianhk/MaxMSP-MCP-Server`](https://github.com/tiianhk/MaxMSP-MCP-Server) with additional features — see "What this fork adds" below.

## What this fork adds

- **More MCP clients supported** — Claude Desktop (upstream), plus **Claude Code** (writes `.mcp.json`), Cursor, and VS Code. Launch the server using an absolute path to the venv's `mcp` binary so clients that don't inherit a user shell (Claude Desktop on macOS) can still find it.
- **Patcher targeting** — work on user patches, not just the agent's own patch. The robust primitive is `watch_for_target_patcher`: it polls `max.frontpatcher` from inside Max and captures whichever non-agent, non-Max-Console patcher the user clicks. Also `set_target_to_front_patcher`, `set_target_patcher_by_name`, `set_target_to_agent_patcher`, `get_target_patcher_info`.
- **Bug fixes & resilience** — scoping fix in `server_lifespan` (`maxmsp` was only defined in one branch), deprecated `asyncio.get_event_loop()` → `get_running_loop()`, Socket.IO auto-reconnection, configurable request timeout, CORS restricted to localhost, null-checks in `connect/disconnect_objects`.
- **Open WebUI RAG integration** — optional `query_max_docs` MCP tool does semantic search against an Open WebUI knowledge base (Max 9 User Reference + JS API + LOM + Node for Max). Gated on `OPENWEBUI_URL` / `OPENWEBUI_API_KEY` / `OPENWEBUI_MAX_COLLECTION_ID` env vars.
- **Embedded Anthropic agent** — the server can run a full agent loop *inside itself*, letting Max be its own chat client (prompts come from a `[textedit]` in Max, responses stream back). Reuses the same `@mcp.tool()`-decorated functions as the external MCP interface. Enabled by `ANTHROPIC_API_KEY`.
- **`.maxpat` file generator** — `maxpat_builder.py` library plus a `create_maxpat_file` MCP tool plus a standalone `maxpat_cli.py` CLI. Writes complete `.maxpat` files to disk from a structured JSON spec — bypasses the live-edit Socket.IO plumbing entirely and works even when Max isn't running. Auto-infers inlet/outlet counts from `docs.json` and a UI lookup table.
- **Richer Max JS diagnostics** — `get_target_patcher_info` also reports `max.frontpatcher`, the agent patcher, whether the target override is null, and which `max.*` APIs are actually available in the running JS engine — all useful when debugging why targeting behaves differently than expected.

See `CLAUDE.md` for architecture details and the complete tool reference.


### Understand: LLM Explaining a Max Patch

![img](./assets/understand.gif)
[Video link](https://www.youtube.com/watch?v=YKXqS66zrec). Acknowledgement: the patch being explained is downloaded from [here](https://github.com/jeffThompson/MaxMSP_TeachingSketches/blob/master/02_MSP/07%20Ring%20Modulation.maxpat). Text comments in the original file are deleted.

### Generate: LLM Making an FM Synth

![img](./assets/generate.gif)
Check out the [full video](https://www.youtube.com/watch?v=Ns89YuE5-to) where you can listen to the synthesised sounds.

The LLM agent has access to the official documentation of each object, as well as objects in the current patch and subpatch windows, which helps in retrieving and explaining objects, debugging, and verifying their own actions.

## Installation  

### Prerequisites  

 - Python 3.8 or newer  
 - [uv package manager](https://github.com/astral-sh/uv)  
 - Max 9 or newer (because some of the scripts require the Javascript V8 engine), we have not tested it on Max 8 or earlier versions of Max yet.  

### Installing the MCP server

1. Install uv:
```
# On macOS and Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh
# On Windows:
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
2. Clone this repository and open its directory:
```
git clone https://github.com/tiianhk/MaxMSP-MCP-Server.git
cd MaxMSP-MCP-Server
```
3. Start a new environment and install python dependencies:
```
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```
4. Connect the MCP server to a MCP client (which hosts LLMs):
```
# Claude Desktop:
python install.py --client claude
# Claude Code (writes .mcp.json in this project):
python install.py --client claude-code
# Cursor:
python install.py --client cursor
# VS Code:
python install.py --client vscode
```
If you have fork-specific env vars set in your shell when you run `install.py` (`OPENWEBUI_URL`, `OPENWEBUI_API_KEY`, `OPENWEBUI_MAX_COLLECTION_ID`, `ANTHROPIC_API_KEY`), they are propagated into the generated client config automatically.

To use other clients (check the [list](https://modelcontextprotocol.io/clients)), you need to download, mannually add the configuration file path to [here](https://github.com/tiianhk/MaxMSP-MCP-Server/blob/main/install.py#L6-L13), and connect by running `python install.py --client {your_client_name}`.

### Installing to a Max patch  

Use or copy from `MaxMSP_Agent/demo.maxpat`. In the first tab, click the `script npm version` message to verify that [npm](https://github.com/npm/cli) is installed. Then click `script npm install` to install the required dependencies. Switch to the second tab to access the agent. Click `script start` to initiate communication with Python. Once connected, you can interact with the LLM interface to have it explain, modify, or create Max objects within the patch.

## Disclaimer

This is a third party implementation and not made by Cycling '74.
