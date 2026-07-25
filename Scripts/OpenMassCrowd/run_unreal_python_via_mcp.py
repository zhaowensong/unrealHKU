#!/usr/bin/env python3
"""Execute a Python script inside the running TelecomTwin Unreal Editor.

The bundled UnrealMCP bridge currently depends on a particular FastMCP version.
This small client talks directly to the project plugin's local JSON socket, so
asset preparation scripts remain reproducible even when that bridge package is
not importable in the system Python environment.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any, Sequence


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 13377
DEFAULT_TIMEOUT_SECONDS = 120.0
# UnrealMCP's protocol has no message framing or multi-packet command
# reassembly. Keep JSON commands below its 64 KiB receive buffer. Local files
# are always executed through a tiny runpy bootstrap instead of being copied
# into the socket payload.
MAX_SAFE_COMMAND_BYTES = 60 * 1024


def receive_json(sock: socket.socket) -> dict[str, Any]:
    payload = bytearray()
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        payload.extend(chunk)
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            continue

    if not payload:
        raise RuntimeError("UnrealMCP closed the connection without a response")
    raise RuntimeError("UnrealMCP returned incomplete JSON")


def execute_python(
    code: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    command = {
        "type": "execute_python",
        "params": {"code": code},
    }
    encoded_command = json.dumps(command).encode("utf-8")
    if len(encoded_command) > MAX_SAFE_COMMAND_BYTES:
        raise RuntimeError(
            "UnrealMCP command is {} bytes, above the safe {}-byte socket "
            "limit; put large code in a local --file so the runner can use "
            "its runpy bootstrap".format(
                len(encoded_command), MAX_SAFE_COMMAND_BYTES
            )
        )

    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.settimeout(timeout_seconds)
        sock.sendall(encoded_command)
        return receive_json(sock)


def _runpy_bootstrap(source_path: Path, script_args: Sequence[str]) -> str:
    """Build a small bootstrap that preserves Unreal Editor's ``sys.argv``."""
    resolved_path = source_path.resolve()
    unreal_safe_path = str(resolved_path).replace("\\", "/")
    forwarded_argv = [unreal_safe_path, *(str(value) for value in script_args)]
    return (
        "import runpy, sys\n"
        "_open_mass_previous_argv = sys.argv\n"
        f"sys.argv = {forwarded_argv!r}\n"
        "try:\n"
        "    try:\n"
        f"        runpy.run_path({unreal_safe_path!r}, run_name='__main__')\n"
        "    except SystemExit as _open_mass_exit:\n"
        "        _open_mass_exit_code = _open_mass_exit.code\n"
        "        if _open_mass_exit_code not in (None, 0):\n"
        "            raise RuntimeError(\n"
        "                'target script exited with status {!r}'.format(\n"
        "                    _open_mass_exit_code\n"
        "                )\n"
        "            ) from _open_mass_exit\n"
        "finally:\n"
        "    sys.argv = _open_mass_previous_argv\n"
    )


def code_for_local_file(
    source_path: Path, script_args: Sequence[str] = ()
) -> str:
    """Return a small on-disk bootstrap for a readable local Python file.

    Besides avoiding the socket limit, this prevents the UnrealMCP handler's
    triple-quoted wrapper from corrupting source containing quote delimiters.
    The target receives a deterministic ``sys.argv`` without permanently
    changing the editor's process arguments.
    """
    resolved_path = source_path.resolve()
    # Decode once on the caller so missing/non-UTF-8 files fail before opening
    # a socket, while execution itself remains inside the Editor process.
    resolved_path.read_text(encoding="utf-8")
    return _runpy_bootstrap(resolved_path, script_args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Python inside the open TelecomTwin Unreal Editor"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="UTF-8 Python script to run")
    source.add_argument("--code", help="Inline Python code to run")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--script-arg",
        action="append",
        default=[],
        help=(
            "Argument forwarded to a --file script. Repeat for multiple "
            "arguments; use --script-arg=VALUE when VALUE begins with '-'."
        ),
    )
    args = parser.parse_args()
    if args.script_arg and args.file is None:
        parser.error("--script-arg requires --file")
    return args


def main() -> int:
    args = parse_args()
    code = args.code
    if args.file is not None:
        code = code_for_local_file(args.file, args.script_arg)

    try:
        response = execute_python(
            code=code,
            host=args.host,
            port=args.port,
            timeout_seconds=args.timeout,
        )
    except (OSError, RuntimeError) as error:
        print(f"UnrealMCP connection failed: {error}", file=sys.stderr)
        return 2

    result = response.get("result") or {}
    output = result.get("output") or ""
    error = result.get("error") or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n")

    if response.get("status") != "success":
        message = response.get("message") or "Unreal Python execution failed"
        print(message, file=sys.stderr)
        if error:
            print(error, file=sys.stderr, end="" if error.endswith("\n") else "\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
