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
from typing import Any


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 13377
DEFAULT_TIMEOUT_SECONDS = 120.0


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

    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.settimeout(timeout_seconds)
        sock.sendall(encoded_command)
        return receive_json(sock)


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    code = args.code
    if args.file is not None:
        code = args.file.resolve().read_text(encoding="utf-8")

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
