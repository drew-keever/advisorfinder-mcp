"""DB acquisition. THIS IS A STUB for Task 2 — Task 3 replaces it with a real
Cloudflare R2 download (see .superpowers/sdd/yes-tingly-fountain/task-3-brief.md).
For now, ensure_db() only ever honors a local file via MCP_DB_PATH; there is no
network fetch path yet.
"""
import os
from pathlib import Path

from . import db


def ensure_db() -> Path:
    """Return a usable local path to mcp_public.db.

    Stub behavior: if MCP_DB_PATH is set and points at an existing file, use it
    directly (this is the dev/test path). Otherwise raise — there is no R2
    download implemented yet.
    """
    path = os.environ.get("MCP_DB_PATH")
    if path and Path(path).exists():
        db.set_db_path(path)
        db.assert_schema_version()
        return Path(path)
    raise RuntimeError("R2 download not implemented yet — set MCP_DB_PATH")
