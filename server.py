# server.py
from mcp.server.fastmcp import FastMCP, Context
from contextlib import asynccontextmanager
import asyncio
import socketio
import aiohttp

from typing import Callable, Any
import logging
import uuid
import os
import json

try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

SOCKETIO_SERVER_URL = os.environ.get("SOCKETIO_SERVER_URL", "http://127.0.0.1")
SOCKETIO_SERVER_PORT = os.environ.get("SOCKETIO_SERVER_PORT", "5002")
NAMESPACE = os.environ.get("NAMESPACE", "/mcp")
SOCKETIO_TIMEOUT = float(os.environ.get("SOCKETIO_TIMEOUT", "2.0"))

OPENWEBUI_URL = os.environ.get("OPENWEBUI_URL", "").rstrip("/")
OPENWEBUI_API_KEY = os.environ.get("OPENWEBUI_API_KEY", "")
OPENWEBUI_MAX_COLLECTION_ID = os.environ.get("OPENWEBUI_MAX_COLLECTION_ID", "")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-opus-4-7")
AGENT_MAX_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS", "12"))

current_dir = os.path.dirname(os.path.abspath(__file__))
docs_path = os.path.join(current_dir, "docs.json")
with open(docs_path, "r") as f:
    docs = json.load(f)
flattened_docs = {}
for obj_list in docs.values():
    for obj in obj_list:
        flattened_docs[obj["name"]] = obj

io_server_started = False
_maxmsp_connection = None


class MaxMSPConnection:
    def __init__(self, server_url: str, server_port: int, namespace: str = NAMESPACE):

        self.server_url = server_url
        self.server_port = server_port
        self.namespace = namespace

        self.sio = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=10,
            reconnection_delay=1,
            reconnection_delay_max=30,
        )
        self._pending = {}  # fetch requests that are not yet completed

        @self.sio.on("response", namespace=self.namespace)
        async def _on_response(data):
            req_id = data.get("request_id")
            fut = self._pending.get(req_id)
            if fut and not fut.done():
                fut.set_result(data.get("results"))

        @self.sio.on("prompt", namespace=self.namespace)
        async def _on_prompt(data):
            text = (data or {}).get("text", "").strip()
            if not text:
                return
            if not (_HAS_ANTHROPIC and ANTHROPIC_API_KEY):
                await self.sio.emit(
                    "agent_status",
                    {"status": "error", "message": "ANTHROPIC_API_KEY not set or anthropic package missing"},
                    namespace=self.namespace,
                )
                return
            asyncio.create_task(run_agent_loop(self, text))

    async def send_command(self, cmd: dict):
        """Send a command to MaxMSP."""
        await self.sio.emit("command", cmd, namespace=self.namespace)
        logging.info(f"Sent to MaxMSP: {cmd}")

    async def send_request(self, payload: dict, timeout=SOCKETIO_TIMEOUT):
        """Send a fetch request to MaxMSP."""
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        payload.update({"request_id": request_id})
        await self.sio.emit("request", payload, namespace=self.namespace)
        logging.info(f"Request to MaxMSP: {payload}")

        try:
            response = await asyncio.wait_for(future, timeout)
            return response
        except asyncio.TimeoutError:
            raise TimeoutError(f"No response received in {timeout} seconds.")
        finally:
            self._pending.pop(request_id, None)

    async def start_server(self) -> None:
        """IMPORTANT: This method should only be called ONCE per application instance.
        Multiple calls can lead to binding multiple ports unnecessarily.
        """
        try:
            # Connect to the server
            full_url = f"{self.server_url}:{self.server_port}"
            await self.sio.connect(full_url, namespaces=self.namespace)
            logging.info(f"Connected to Socket.IO server at {full_url}")
            return

        except OSError as e:
            logging.error(f"Error starting Socket.IO server: {e}")


@asynccontextmanager
async def server_lifespan(server: FastMCP):
    """Manage server lifespan"""
    global io_server_started, _maxmsp_connection
    if not io_server_started:
        maxmsp = MaxMSPConnection(
            SOCKETIO_SERVER_URL, SOCKETIO_SERVER_PORT, NAMESPACE
        )
        try:
            await maxmsp.start_server()
            io_server_started = True
            _maxmsp_connection = maxmsp
            logging.info(f"Listening on {maxmsp.server_url}:{maxmsp.server_port}")
            yield {"maxmsp": maxmsp}
        except Exception as e:
            logging.error(f"lifespan error starting server: {e}")
            raise
        finally:
            logging.info("Shutting down connection")
            io_server_started = False
            _maxmsp_connection = None
            await maxmsp.sio.disconnect()
    else:
        logging.info(
            f"IO server already running on {_maxmsp_connection.server_url}:{_maxmsp_connection.server_port}"
        )
        yield {"maxmsp": _maxmsp_connection}


# Create the MCP server with lifespan support
mcp = FastMCP(
    "MaxMSPMCP",
    description="MaxMSP integration through the Model Context Protocol",
    lifespan=server_lifespan,
)


@mcp.tool()
async def add_max_object(
    ctx: Context,
    position: list,
    obj_type: str,
    varname: str,
    args: list,
):
    """Add a new Max object.

    The position is is a list of two integers representing the x and y coordinates,
    which should be outside the rectangular area returned by get_avoid_rect_position() function.

    Args:
        position (list): Position in the Max patch as [x, y].
        obj_type (str): Type of the Max object (e.g., "cycle~", "dac~").
        varname (str): Variable name for the object.
        args (list): Arguments for the object.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    assert len(position) == 2, "Position must be a list of two integers."
    cmd = {"action": "add_object"}
    kwargs = {
        "position": position,
        "obj_type": obj_type,
        "args": args,
        "varname": varname,
    }
    cmd.update(kwargs)
    await maxmsp.send_command(cmd)


@mcp.tool()
async def remove_max_object(
    ctx: Context,
    varname: str,
):
    """Delete a Max object.

    Args:
        varname (str): Variable name for the object.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    cmd = {"action": "remove_object"}
    kwargs = {"varname": varname}
    cmd.update(kwargs)
    await maxmsp.send_command(cmd)


@mcp.tool()
async def connect_max_objects(
    ctx: Context,
    src_varname: str,
    outlet_idx: int,
    dst_varname: str,
    inlet_idx: int,
):
    """Connect two Max objects.

    Args:
        src_varname (str): Variable name of the source object.
        outlet_idx (int): Outlet index on the source object.
        dst_varname (str): Variable name of the destination object.
        inlet_idx (int): Inlet index on the destination object.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    cmd = {"action": "connect_objects"}
    kwargs = {
        "src_varname": src_varname,
        "outlet_idx": outlet_idx,
        "dst_varname": dst_varname,
        "inlet_idx": inlet_idx,
    }
    cmd.update(kwargs)
    await maxmsp.send_command(cmd)


@mcp.tool()
async def disconnect_max_objects(
    ctx: Context,
    src_varname: str,
    outlet_idx: int,
    dst_varname: str,
    inlet_idx: int,
):
    """Disconnect two Max objects.

    Args:
        src_varname (str): Variable name of the source object.
        outlet_idx (int): Outlet index on the source object.
        dst_varname (str): Variable name of the destination object.
        inlet_idx (int): Inlet index on the destination object.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    cmd = {"action": "disconnect_objects"}
    kwargs = {
        "src_varname": src_varname,
        "outlet_idx": outlet_idx,
        "dst_varname": dst_varname,
        "inlet_idx": inlet_idx,
    }
    cmd.update(kwargs)
    await maxmsp.send_command(cmd)


@mcp.tool()
async def set_object_attribute(
    ctx: Context,
    varname: str,
    attr_name: str,
    attr_value: list,
):
    """Set an attribute of a Max object.

    Args:
        varname (str): Variable name of the object.
        attr_name (str): Name of the attribute to be set.
        attr_value (list): Values of the attribute to be set.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    cmd = {"action": "set_object_attribute"}
    kwargs = {"varname": varname, "attr_name": attr_name, "attr_value": attr_value}
    cmd.update(kwargs)
    await maxmsp.send_command(cmd)


@mcp.tool()
async def set_message_text(
    ctx: Context,
    varname: str,
    text_list: list,
):
    """Set the text of a message object in MaxMSP.

    Args:
        varname (str): Variable name of the message object.
        text_list (list): A list of arguments to be set to the message object.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    cmd = {"action": "set_message_text"}
    kwargs = {"varname": varname, "new_text": text_list}
    cmd.update(kwargs)
    await maxmsp.send_command(cmd)


@mcp.tool()
async def send_bang_to_object(ctx: Context, varname: str):
    """Send a bang to an object in MaxMSP.

    Args:
        varname (str): Variable name of the object to be banged.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    cmd = {"action": "send_bang_to_object"}
    kwargs = {"varname": varname}
    cmd.update(kwargs)
    await maxmsp.send_command(cmd)


@mcp.tool()
async def send_messages_to_object(
    ctx: Context,
    varname: str,
    message: list,
):
    """Send a message to an object in MaxMSP. The message is made of a list of arguments.

    When using message to set attributes, one attribute can only be set by one message.
    For example, to set the "size" attribute of a "button" object, use:
    send_messages_to_object("button1", ["size", 100, 100])
    To set the "size" and "color" attributes of a "button" object, use the tool for two times:
    send_messages_to_object("button1", ["size", 100, 100])
    send_messages_to_object("button1", ["color", 0, 0, 0])

    Args:
        varname (str): Variable name of the object to be messaged.
        message (list): A list of messages to be sent to the object.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    cmd = {"action": "send_message_to_object"}
    kwargs = {"varname": varname, "message": message}
    cmd.update(kwargs)
    await maxmsp.send_command(cmd)


@mcp.tool()
async def set_number(
    ctx: Context,
    varname: str,
    num: float,
):
    """Set the value of a object in MaxMSP.
    The object can be a number box, a slider, a dial, a gain.

    Args:
        varname (str): Variable name of the comment object.
        num (float): Value to be set for the object.
    """

    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    cmd = {"action": "set_number"}
    kwargs = {"varname": varname, "num": num}
    cmd.update(kwargs)
    await maxmsp.send_command(cmd)


@mcp.tool()
def list_all_objects(ctx: Context) -> list:
    """Returns a name list of all objects that can be added in Max.
    To understand a specific object in the list, use the `get_object_doc` tool."""
    return list(flattened_docs.keys())


@mcp.tool()
def get_object_doc(ctx: Context, object_name: str) -> dict:
    """Retrieve the official documentation for a given object.
    Use this resource to understand how a specific object works, including its
    description, inlets, outlets, arguments, methods(messages), and attributes.

    Args:
        object_name (str): Name of the object to look up.

    Returns:
        dict: Official documentations for the specified object.
    """
    try:
        return flattened_docs[object_name]
    except KeyError:
        return {
            "success": False,
            "error": "Invalid object name",
            "suggestion": "Make sure the object name is a valid Max object name.",
        }


@mcp.tool()
async def get_objects_in_patch(
    ctx: Context,
):
    """Retrieve the list of existing objects in the current Max patch.

    Use this to understand the current state of the patch, including the
    objects(boxes) and patch cords(lines). The retrieved list contains a
    list of objects including their maxclass, varname for scripting,
    position(patching_rect), and the boxtext when available, as well as a
    list of patch cords with their source and destination information.

    Returns:
        list: A list of objects and patch cords.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    payload = {"action": "get_objects_in_patch"}
    response = await maxmsp.send_request(payload)

    return [response]


@mcp.tool()
async def get_objects_in_selected(
    ctx: Context,
):
    """Retrieve the list of objects that is selected in a (unlocked) patcher window.

    Use this when the user wanted to reference to the selected objects.

    Returns:
        list: A list of objects and patch cords.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    payload = {"action": "get_objects_in_selected"}
    response = await maxmsp.send_request(payload)

    return [response]


@mcp.tool()
async def get_object_attributes(ctx: Context, varname: str):
    """Retrieve an objects' attributes and values of the attributes.

    Use this to understand the state of an object.

    Returns:
        list: A list of attributes name and attributes values.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    payload = {"action": "get_object_attributes"}
    kwargs = {"varname": varname}
    payload.update(kwargs)
    response = await maxmsp.send_request(payload)

    return [response]


@mcp.tool()
async def set_target_to_front_patcher(ctx: Context):
    """Retarget all subsequent patch operations to Max's current front (focused) patcher.

    Use this when the user wants the agent to work on a patch other than the one
    containing the agent UI. Bring the desired patcher to the front in Max, then
    call this tool. The target stays locked to that patcher until changed.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    await maxmsp.send_command({"action": "set_target_to_front"})


@mcp.tool()
async def set_target_to_agent_patcher(ctx: Context):
    """Reset the target patcher back to the one containing the agent UI.

    Use this to return to the default behavior after working on an external patch.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    await maxmsp.send_command({"action": "set_target_to_agent"})


@mcp.tool()
async def watch_for_target_patcher(ctx: Context, timeout_sec: int = 15):
    """Start a polling task inside Max that captures the user's chosen target patcher.

    Max's `max.frontpatcher` only returns a valid reference while Max itself has OS focus,
    so we can't read it from an external MCP call. This tool starts a Task inside Max that
    checks max.frontpatcher every 100ms for `timeout_sec` seconds. When the user brings any
    patcher other than the agent or Max Console to the front in Max, it gets captured as the
    target for all subsequent patch operations.

    Typical workflow:
      1. Call this tool.
      2. Switch to Max and click anywhere in the patcher you want to work on.
      3. Capture is immediate; you can then switch back and continue in the MCP client.

    Args:
        timeout_sec (int): How long to wait for a click before giving up (default 15).
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    await maxmsp.send_command({"action": "watch_for_target", "timeout_sec": timeout_sec})


@mcp.tool()
async def set_target_patcher_by_name(ctx: Context, name: str):
    """Retarget all subsequent patch operations to an open patcher, identified by its
    filename (without extension).

    Max's `max.frontpatcher` is only valid when Max itself has OS focus, which makes
    `set_target_to_front_patcher` unreliable from an external MCP client. Saving the
    target patcher to disk and passing its filename here is the robust alternative.

    Args:
        name (str): Patcher filename without extension (e.g. for "mywork.maxpat" pass "mywork").
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    await maxmsp.send_command({"action": "set_target_by_name", "name": name})


@mcp.tool()
async def get_target_patcher_info(ctx: Context):
    """Return info about the currently targeted patcher (title, filepath, whether it is the agent patch).

    Use this to confirm which patcher the agent is currently acting on.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    payload = {"action": "get_target_info"}
    response = await maxmsp.send_request(payload)
    return response


@mcp.tool()
async def get_avoid_rect_position(ctx: Context):
    """When deciding the position to add a new object to the path, this rectangular area
    should be avoid. This is useful when you want to add an object to the patch without
    overlapping with existing objects.

    Returns:
        list: A list of four numbers representing the left, top, right, bottom of the rectangular area.
    """
    maxmsp = ctx.request_context.lifespan_context.get("maxmsp")
    payload = {"action": "get_avoid_rect_position"}
    response = await maxmsp.send_request(payload)

    return response


if OPENWEBUI_URL and OPENWEBUI_API_KEY and OPENWEBUI_MAX_COLLECTION_ID:

    @mcp.tool()
    async def query_max_docs(ctx: Context, query: str, k: int = 5) -> list:
        """Search the Max 9 User Reference knowledge base for documentation relevant to a query.

        Use this when you need authoritative information about Max/MSP objects, messages,
        attributes, tutorials, or concepts beyond what get_object_doc provides. Returns the
        top-k most relevant manual excerpts with their source filenames and similarity scores.

        Args:
            query (str): Natural-language search query (e.g. "how do I use the matrix~ object for audio routing").
            k (int): Number of chunks to return (default 5).

        Returns:
            list: Items of the form {"source": str, "score": float, "text": str}, sorted by score descending.
        """
        url = f"{OPENWEBUI_URL}/api/v1/retrieval/query/collection"
        payload = {
            "collection_names": [OPENWEBUI_MAX_COLLECTION_ID],
            "query": query,
            "k": k,
        }
        headers = {
            "Authorization": f"Bearer {OPENWEBUI_API_KEY}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return [{"error": f"HTTP {resp.status}: {body[:500]}"}]
                data = await resp.json()

        docs = (data.get("documents") or [[]])[0]
        metas = (data.get("metadatas") or [[]])[0]
        results = []
        for text, meta in zip(docs, metas):
            results.append({
                "source": (meta or {}).get("source", ""),
                "score": (meta or {}).get("score"),
                "text": text,
            })
        return results


@mcp.tool()
def create_maxpat_file(
    ctx: Context,
    path: str,
    boxes: list,
    lines: list,
    rect: list = None,
) -> dict:
    """Generate a complete .maxpat file from a structured spec and write it to disk.

    Use this when the user wants a whole patch built from scratch (not incremental edits to
    an open patcher). The generated file can be opened in Max via File > Open. Unlike the
    live-edit tools (add_max_object etc.), this bypasses the MCP->Max Socket.IO plumbing
    entirely and produces a standalone file.

    Args:
        path (str): Absolute output path, should end with .maxpat.
        boxes (list): List of box specs. Each is a dict with keys:
            - maxclass (str): "newobj" for most objects, or UI class like "flonum",
              "ezdac~", "comment", "message", "button", "toggle", "dial", "slider", etc.
            - text (str, optional): The object text, e.g. "cycle~ 440" for newobj,
              or the comment body for comment. Omit for most UI objects.
            - varname (str, optional): Scripting name used to reference this box in `lines`
              and for later set_object_attribute-style calls.
            - patching_rect (list[float], optional): [x, y, width, height]. Provide this
              for every object unless you want Max's defaults.
            - numinlets / numoutlets (int, optional): Override auto-inferred inlet counts.
            - Other Max attributes can be passed as kwargs (e.g. format=6, parameter_enable=0).
        lines (list): List of connection specs. Each is a dict with keys:
            - src (str): Source varname (or id like "obj-1") — must match a box.
            - dst (str): Destination varname (or id).
            - src_outlet (int, optional): Source outlet index, default 0.
            - dst_inlet (int, optional): Destination inlet index, default 0.
        rect (list[float], optional): Patcher window rect [x, y, w, h].
            Default [100, 100, 1000, 780].

    Returns:
        dict: {"path": <str>, "num_boxes": <int>, "num_lines": <int>}
    """
    from maxpat_builder import Patch

    p = Patch(rect=tuple(rect) if rect else (100.0, 100.0, 1000.0, 780.0))
    for b in boxes:
        p.add(**b)
    for line in lines:
        p.connect(
            src=line["src"],
            dst=line["dst"],
            src_outlet=int(line.get("src_outlet", 0)),
            dst_inlet=int(line.get("dst_inlet", 0)),
        )
    p.save(path)
    return {"path": path, "num_boxes": len(boxes), "num_lines": len(lines)}


AGENT_SYSTEM_PROMPT = """You are a creative collaborator helping the user build Max/MSP patches in real-time from inside Max itself.

Tools available:
- query_max_docs: semantic search over the Max 9 User Reference (use this before non-trivial choices)
- get_object_doc / list_all_objects: exact object reference pages
- get_objects_in_patch / get_object_attributes: read current patch state
- add_max_object / remove_max_object / connect_max_objects / disconnect_max_objects
- set_object_attribute / set_message_text / send_messages_to_object / send_bang_to_object / set_number
- watch_for_target_patcher / set_target_patcher_by_name / get_target_patcher_info

Conventions:
- Before choosing objects, call query_max_docs to see how the manual recommends doing what was asked.
- Use semantic varnames: osc1/osc2/..., lfo1/..., gain, out, filter1, env1, rev/del/dist. UI controls use <param>_ctl.
- Layout: controls above their target; signal flows top-to-bottom; audio output at the bottom.
- Audio output scaffold: <source> -> [*~ 0.2] (varname: gain) -> [ezdac~] (varname: out), both channels.
- Session start: if no target is set or is_agent=true, ask the user to bring their target patcher to front in Max, then call watch_for_target_patcher.

Communication style: say in one short sentence what you're about to do, then do it. No preamble, no long summaries. Cite manual section filenames when query_max_docs returned them.
"""


def _agent_tools_from_mcp():
    """Convert FastMCP-registered tools to the Anthropic tool schema format."""
    tools = []
    for tool_name, tool in getattr(mcp._tool_manager, "_tools", {}).items():
        schema = getattr(tool, "parameters", None) or {"type": "object", "properties": {}}
        tools.append(
            {
                "name": tool.name,
                "description": (tool.description or "").strip() or tool.name,
                "input_schema": schema,
            }
        )
    return tools


class _AgentContext:
    """Minimal stand-in for FastMCP's Context — exposes request_context.lifespan_context['maxmsp']."""

    def __init__(self, maxmsp):
        class _Req:
            pass
        self.request_context = _Req()
        self.request_context.lifespan_context = {"maxmsp": maxmsp}


async def _dispatch_agent_tool(maxmsp, name: str, tool_input: dict):
    tool = getattr(mcp._tool_manager, "_tools", {}).get(name)
    if not tool:
        return {"error": f"Unknown tool: {name}"}
    ctx = _AgentContext(maxmsp)
    try:
        result = tool.fn(ctx, **(tool_input or {}))
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except Exception as e:
        logging.exception("Agent tool dispatch error")
        return {"error": f"Tool {name} raised: {e}"}


async def run_agent_loop(maxmsp, user_prompt: str):
    """Run the embedded Anthropic agent loop, streaming text back to Max via Socket.IO."""
    ns = maxmsp.namespace
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    tools = _agent_tools_from_mcp()
    messages = [{"role": "user", "content": user_prompt}]

    await maxmsp.sio.emit("agent_status", {"status": "thinking"}, namespace=ns)

    try:
        for _ in range(AGENT_MAX_ITERATIONS):
            async with client.messages.stream(
                model=AGENT_MODEL,
                max_tokens=16000,
                system=[
                    {
                        "type": "text",
                        "text": AGENT_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive"},
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        await maxmsp.sio.emit(
                            "agent_text",
                            {"text": event.delta.text},
                            namespace=ns,
                        )
                response = await stream.get_final_message()

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                await maxmsp.sio.emit(
                    "agent_status",
                    {"status": "done", "stop_reason": response.stop_reason},
                    namespace=ns,
                )
                return

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                await maxmsp.sio.emit(
                    "agent_tool_use",
                    {"name": block.name, "input": block.input},
                    namespace=ns,
                )
                result = await _dispatch_agent_tool(maxmsp, block.name, block.input)
                try:
                    content = json.dumps(result, default=str) if result is not None else "ok"
                except Exception:
                    content = str(result)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        await maxmsp.sio.emit(
            "agent_status",
            {"status": "error", "message": f"Max iterations ({AGENT_MAX_ITERATIONS}) reached"},
            namespace=ns,
        )
    except Exception as e:
        logging.exception("Agent loop error")
        await maxmsp.sio.emit(
            "agent_status",
            {"status": "error", "message": str(e)},
            namespace=ns,
        )


if __name__ == "__main__":
    mcp.run()
