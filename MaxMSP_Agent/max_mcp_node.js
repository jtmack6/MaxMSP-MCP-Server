autowatch = 1;

const Max = require("max-api");
const { Server } = require("socket.io");

// Configuration
var PORT = 5002;
const NAMESPACE = "/mcp";

// Create Socket.IO server
var io = new Server(PORT, {
  cors: { origin: ["http://127.0.0.1", "http://localhost"] }
});

Max.outlet("port", `Server listening on port ${PORT}`);

function safe_parse_json(str) {
    try {
        return JSON.parse(str);
    } catch (e) {
        Max.post("error, Invalid JSON: " + e.message);
        Max.post("This is likely because the patcher has too much objects, select some of them and try again");
        return null;
    }
}

Max.addHandler("response", async (...msg) => {
	var str = msg.join("")
	var data = safe_parse_json(str);
	await io.of(NAMESPACE).emit("response", data);
	// await Max.post(`Sent response: ${JSON.stringify(data)}`);
});

// Forward user prompts from Max to the Python agent.
// In Max: send `prompt <your text>` to this node.script object.
Max.addHandler("prompt", async (...msg) => {
    var text = msg.join(" ");
    await io.of(NAMESPACE).emit("prompt", { text: text });
});

Max.addHandler("port", async (msg) => {
  Max.post(`msg ${msg}`);
  if (msg > 0 && msg < 65536) {
    PORT = msg;
  }
  await io.close();
  io = new Server(PORT, {
    cors: { origin: ["http://127.0.0.1", "http://localhost"] }
  });
  // await Max.post(`Socket.IO MCP server listening on port ${PORT}`);
  await Max.outlet("port", `Server listening on port ${PORT}`);
});

io.of(NAMESPACE).on("connection", (socket) => {
  Max.post(`Socket.IO client connected: ${socket.id}`);

  socket.on("command", async (data) => {
    // Max.post(`Socket.IO command received: ${data}`);
	  Max.outlet("command", JSON.stringify(data)); 
  });

  socket.on("request", async (data) => {
	  Max.outlet("request", JSON.stringify(data));
  });

  // Agent-loop events streamed from the Python server back to Max.
  // Route these to their own outlets so a Max patch can display them.
  socket.on("agent_text", async (data) => {
      Max.outlet("agent_text", (data && data.text) || "");
  });

  socket.on("agent_status", async (data) => {
      Max.outlet("agent_status", JSON.stringify(data || {}));
  });

  socket.on("agent_tool_use", async (data) => {
      Max.outlet("agent_tool_use", JSON.stringify(data || {}));
  });

  socket.on("port", async (data) => {
    Max.post(`msg ${data}`);
    if (data > 0 && data < 65536) {
      PORT = data;
    }
    await io.close();
    io = new Server(PORT, {
      cors: { origin: ["http://127.0.0.1", "http://localhost"] }
    });
    // await Max.post(`Socket.IO MCP server listening on port ${PORT}`);
    await Max.outlet("port", `Server listening on port ${PORT}`);
  });
  

  socket.on("disconnect", () => {
    Max.post(`Socket.IO client disconnected: ${socket.id}`);
  });
});
