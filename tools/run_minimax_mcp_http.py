"""Run the locally installed MiniMax MCP server as an independent HTTP service.

This launcher is intentionally outside OpenNexus Core.  It reads the existing
legacy encrypted MCP credential without printing it, then starts the upstream
server on loopback using Streamable HTTP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def credential_id(server_id: str, key: str) -> str:
    identity = f"environment-v2\0{key}"
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:20]
    return f"mcp.{server_id}.{suffix}"


def load_secret(directory: Path, identity: str) -> str:
    try:
        key = (directory / "master.key").read_bytes().strip()
        tokens = json.loads((directory / "credentials.json").read_text(encoding="utf-8"))
        token = tokens[identity]
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (OSError, KeyError, ValueError, InvalidToken, UnicodeError) as exc:
        raise SystemExit("MINIMAX_MCP_CREDENTIAL_UNAVAILABLE") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent MiniMax MCP HTTP server")
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--server-id", default="9ca7ee21603a")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.host != "127.0.0.1" or not 1 <= args.port <= 65535:
        raise SystemExit("MINIMAX_MCP_LOOPBACK_REQUIRED")

    secret = load_secret(
        args.credentials_dir,
        credential_id(args.server_id, "MINIMAX_API_KEY"),
    )
    os.environ["MINIMAX_API_KEY"] = secret
    os.environ.setdefault("MINIMAX_API_HOST", "https://api.minimaxi.com")
    os.environ.setdefault("FASTMCP_LOG_LEVEL", "WARNING")

    from minimax_mcp.server import mcp

    mcp.run(
        "streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path="/mcp",
        stateless_http=False,
    )


if __name__ == "__main__":
    main()
