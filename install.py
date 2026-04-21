import os
import json
import argparse
from pathlib import Path

CONFIG_PATHS = {
    "claude": (
        "~/Library/Application Support/Claude/claude_desktop_config.json"
        if os.name == "posix"  # macOS or Linux
        else r"%APPDATA%\Claude\claude_desktop_config.json"  # Windows
    ),
    "claude-code": ".mcp.json",
    "cursor": "~/.cursor/mcp.json",
    "vscode": ".vscode/mcp.json"
}


def expand_path(path):
    # Expand ~
    path = os.path.expanduser(path)
    # Expand environment variables like %APPDATA%
    path = os.path.expandvars(path)
    # Normalize and convert to absolute path
    return os.path.abspath(path)


def load_json(file_path: Path):
    # Not exist or is empty
    if not file_path.exists() or file_path.stat().st_size == 0:
        # Create the file with an empty JSON object
        with open(file_path, "w") as f:
            json.dump({"mcpServers": {}}, f)
    # Load the JSON data
    with open(file_path, "r") as f:
        return json.load(f)


OPENWEBUI_ENV_KEYS = ("OPENWEBUI_URL", "OPENWEBUI_API_KEY", "OPENWEBUI_MAX_COLLECTION_ID")


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--client",
        type=str,
        required=True,
        choices=list(CONFIG_PATHS.keys()),
        help=f"Supported clients: {', '.join(CONFIG_PATHS.keys())}",
    )
    args = parser.parse_args()
    config_path = Path(expand_path(CONFIG_PATHS[args.client]))
    config_data = load_json(config_path)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(os.path.join(current_dir, ".venv")):
        raise FileNotFoundError("Use uv to create a virtual environment first. ")

    mcp_name = "servers" if args.client == "vscode" else "mcpServers"

    env = {
        "PATH": os.path.join(current_dir, ".venv/bin"),
        "VIRTUAL_ENV": os.path.join(current_dir, ".venv"),
    }
    for key in OPENWEBUI_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value

    config_data[mcp_name]["MaxMSPMCP"] = {
        "command": "mcp",
        "args": ["run", os.path.join(current_dir, "server.py")],
        "env": env,
    }

    with open(config_path, "w") as f:
        json.dump(config_data, f, indent=4)


if __name__ == "__main__":
    main()
