"""Server-import smoke test.

Imports the FastMCP server entry module and asserts it constructs. This is the
runtime-verification gate the install step alone does not provide: a dependency
bump (e.g. a fastmcp / starlette major) that breaks tool registration or server
construction fails here instead of shipping silently. See MYC-660.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_server_module_constructs() -> None:
    from fastmcp import FastMCP
    import server
    assert isinstance(server.mcp, FastMCP), f"server.mcp is not FastMCP: {type(server.mcp)!r}"
