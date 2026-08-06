"""DB acquisition for advisorfinder_mcp.

ensure_db() -> Path is the single entry point server.main() calls before
serving traffic:

  1. Dev/test short-circuit: if MCP_DB_PATH is set and points at an existing
     file, use it directly — no network, no R2 credentials needed. This is
     the path conftest.py's session fixture (and 122+ existing tests) rely
     on; it is intentionally checked first and unconditionally, before any
     R2 env var is even read.
  2. Otherwise, download mcp_public.db from a private Cloudflare R2 bucket
     (S3-compatible API) via boto3, verifying it against a small manifest.json
     (sha256 + sizeBytes + schemaVersion) before ever pointing db.py at it.
     A restart fast-path skips re-downloading when a sidecar `{db}.sha256`
     file already matches the manifest's sha256.

boto3 is imported lazily, INSIDE the R2 branch: the PyPI proxy package
(advisorfinder_mcp.proxy, installed via plain `pip install advisorfinder-mcp`)
has no boto3 dependency and never calls ensure_db() at all, so a module-level
`import boto3` here would break that install for no reason.

Any integrity failure (sha256 mismatch, size mismatch, schema version
mismatch) raises RuntimeError and leaves no half-verified file in place —
the server must not start against unverified or wrong-schema data.
"""
import hashlib
import json
import os
from pathlib import Path

from . import SCHEMA_VERSION, db

# Streamed download chunk size, per task-3-brief.md ("8MB chunks").
_CHUNK_SIZE = 8 * 1024 * 1024

_DEFAULT_BUCKET = "advisorfinder-mcp"
_DEFAULT_DB_KEY = "mcp_public.db"
_DEFAULT_MANIFEST_KEY = "manifest.json"
_DEFAULT_DB_DIR = "/tmp/adfi"

_REQUIRED_R2_ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


def _require_r2_env() -> dict:
    missing = [name for name in _REQUIRED_R2_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "R2 download requires environment variable(s) not set: "
            + ", ".join(missing)
            + " (or set MCP_DB_PATH to a local file for dev/test)"
        )
    return {name: os.environ[name] for name in _REQUIRED_R2_ENV}


def _build_client(account_id: str, access_key_id: str, secret_access_key: str):
    import boto3  # lazy: not a dependency of the PyPI stdio-proxy install

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )


def ensure_db(client=None) -> Path:
    """Return a usable local path to mcp_public.db, downloading it from R2
    first if necessary.

    `client` is an injectable S3-shaped object exposing
    `get_object(Bucket=..., Key=...) -> {"Body": <has .read(n)>}` — the same
    shape as a real boto3 S3 client's response. Defaults to a real boto3
    client built from the R2_* env vars. Tests inject a fake to exercise the
    download/verify logic with zero network and zero boto3 dependency.
    """
    mcp_db_path = os.environ.get("MCP_DB_PATH")
    if mcp_db_path and Path(mcp_db_path).exists():
        db.set_db_path(mcp_db_path)
        db.assert_schema_version()
        return Path(mcp_db_path)

    creds = _require_r2_env()
    bucket = os.environ.get("R2_BUCKET", _DEFAULT_BUCKET)
    db_key = os.environ.get("R2_DB_KEY", _DEFAULT_DB_KEY)
    manifest_key = os.environ.get("R2_MANIFEST_KEY", _DEFAULT_MANIFEST_KEY)
    db_dir = Path(os.environ.get("DB_DIR", _DEFAULT_DB_DIR))

    if client is None:
        client = _build_client(
            creds["R2_ACCOUNT_ID"], creds["R2_ACCESS_KEY_ID"], creds["R2_SECRET_ACCESS_KEY"]
        )

    manifest_obj = client.get_object(Bucket=bucket, Key=manifest_key)
    manifest = json.loads(manifest_obj["Body"].read())
    expected_sha = manifest["sha256"]
    expected_size = manifest["sizeBytes"]
    manifest_schema_version = manifest["schemaVersion"]

    db_dir.mkdir(parents=True, exist_ok=True)
    final_path = db_dir / _DEFAULT_DB_KEY
    sidecar_path = db_dir / f"{_DEFAULT_DB_KEY}.sha256"
    tmp_path = db_dir / f"{_DEFAULT_DB_KEY}.tmp"

    reused = (
        final_path.exists()
        and sidecar_path.exists()
        and sidecar_path.read_text().strip() == expected_sha
    )

    if not reused:
        obj = client.get_object(Bucket=bucket, Key=db_key)
        body = obj["Body"]
        hasher = hashlib.sha256()
        size = 0
        try:
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = body.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    size += len(chunk)
                    f.write(chunk)

            actual_sha = hasher.hexdigest()
            if actual_sha != expected_sha or size != expected_size:
                raise RuntimeError(
                    "mcp_public.db integrity check failed: "
                    f"sha256={actual_sha} (expected {expected_sha}), "
                    f"size={size} (expected {expected_size})"
                )
            os.replace(tmp_path, final_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    # set_db_path + assert_schema_version run whether we just downloaded or
    # reused the cached file — both paths must prove the DB on disk actually
    # matches this server's expected schema before anything queries it.
    db.set_db_path(final_path)
    db.assert_schema_version()
    if int(manifest_schema_version) != SCHEMA_VERSION:
        raise RuntimeError(
            f"manifest schemaVersion={manifest_schema_version!r} does not match "
            f"this server's SCHEMA_VERSION={SCHEMA_VERSION} — redeploy a matching export"
        )

    # Sidecar is written ONLY after a fresh download has passed every check
    # above (sha/size during streaming, schema version just now) — never
    # written on the reuse path (nothing changed, sidecar already matches)
    # and never written before the schemaVersion cross-check, so a
    # schema-mismatched download can't poison the restart fast-path into
    # reusing bad data next boot without re-verifying.
    if not reused:
        sidecar_path.write_text(expected_sha)

    return final_path
