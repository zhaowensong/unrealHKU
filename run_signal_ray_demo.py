import argparse
import json
import os
import socket
import sys
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 13377


def project_root() -> Path:
    return Path(__file__).resolve().parent


def send_mcp_command(command_type: str, params: dict, host: str, port: int, timeout: float) -> dict:
    token = os.environ.get("TELECOMTWIN_MCP_TOKEN", "").strip()
    if not token:
        token_path = project_root() / "Saved" / "MCP" / "auth-token.txt"
        if token_path.exists():
            token = token_path.read_text(encoding="utf-8").strip()
    payload = json.dumps({"type": command_type, "params": params, "auth_token": token}).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(payload)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            data = b"".join(chunks)
            try:
                return json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue
    raise RuntimeError("No complete JSON response received from Unreal MCP.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ask the running Unreal Editor MCP server to rebuild the Signal Ray Demo."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Unreal MCP host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Unreal MCP port. Default: 13377")
    parser.add_argument("--timeout", type=float, default=300.0, help="Socket timeout in seconds. Default: 300")
    parser.add_argument(
        "--script",
        default=str(project_root() / "Scripts" / "SignalRayDemo" / "build_signal_simulation.py"),
        help="Path to the Unreal Python script to execute.",
    )
    args = parser.parse_args()

    script_path = Path(args.script).resolve()
    if not script_path.exists():
        print(f"Script not found: {script_path}", file=sys.stderr)
        return 2

    try:
        response = send_mcp_command(
            "execute_python",
            {"file": str(script_path).replace("\\", "/")},
            args.host,
            args.port,
            args.timeout,
        )
    except ConnectionRefusedError:
        print(
            f"Could not connect to Unreal MCP at {args.host}:{args.port}. "
            "Open TelecomTwin in Unreal Editor and make sure the UnrealMCP server is running.",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:
        print(f"Failed to communicate with Unreal MCP: {exc}", file=sys.stderr)
        return 4

    status = response.get("status")
    result = response.get("result", {})
    output = result.get("output", "")
    error = result.get("error", "")

    if output:
        print(output.strip())
    if status != "success":
        if error:
            print(error.strip(), file=sys.stderr)
        else:
            print(json.dumps(response, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
