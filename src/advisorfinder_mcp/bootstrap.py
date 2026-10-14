"""DB acquisition for advisorfinder_mcp.

ensure_db() -> Path is the single entry point server.main() calls before
serving traffic:

  1. Dev/test short-circuit: if MCP_DB_PATH is set and points at an existing
     file, use it directly — no network, no R2 credentials needed. This is
     the path conftest.py's session fixture (and most of this package's test
     suite) relies on; it is intentionally checked first and unconditionally,
     before any R2 env var is even read.
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

Every phase logs one INFO line on the `advisorfinder_mcp.bootstrap` logger
with elapsed seconds since ensure_db() started (Render showed ~9 silent
minutes before uvicorn bound its port; these lines say which step is slow).
Log lines carry only bucket/key names, sizes and timings — never
credentials, the account ID, or any URL.
"""
import hashlib
import json
import logging
import os
import time
from pathlib import Path

from . import SCHEMA_VERSION, db

log = logging.getLogger(__name__)

# Streamed download chunk size (8 MiB).
_CHUNK_SIZE = 8 * 1024 * 1024

# Log download progress every this many bytes (not per chunk).
_PROGRESS_EVERY = 64 * 1024 * 1024

_MIB = 1024 * 1024

_DEFAULT_BUCKET = "advisorfinder-mcp"
_DEFAULT_DB_KEY = "mcp_public.db"  # default remote object key (R2_DB_KEY overrides)
_DEFAULT_MANIFEST_KEY = "manifest.json"
_DEFAULT_DB_DIR = "/tmp/adfi"

# Local on-disk filename, independent of R2_DB_KEY: whatever the remote
# object is called, it always lands at {DB_DIR}/mcp_public.db locally, so
# db.py, the sidecar, and the restart fast-path all have one fixed name to
# look for.
_LOCAL_DB_NAME = "mcp_public.db"

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
    t0 = time.monotonic()

    def _log(msg, *args):
        log.info("[bootstrap +%.1fs] " + msg, time.monotonic() - t0, *args)

    _log("start")
    mcp_db_path = os.environ.get("MCP_DB_PATH")
    if mcp_db_path and Path(mcp_db_path).exists():
        _log("using local MCP_DB_PATH (no download)")
        db.set_db_path(mcp_db_path)
        _log("schema check start")
        db.assert_schema_version()
        _log("schema check ok (schema_version=%d); DB ready", SCHEMA_VERSION)
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

    _log("manifest fetch start (bucket=%s key=%s)", bucket, manifest_key)
    manifest_obj = client.get_object(Bucket=bucket, Key=manifest_key)
    try:
        manifest = json.loads(manifest_obj["Body"].read())
        expected_sha = manifest["sha256"]
        expected_size = int(manifest["sizeBytes"])
        manifest_schema_version = int(manifest["schemaVersion"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"corrupt or incomplete manifest.json: {exc!r}") from exc
    _log(
        "manifest fetched (sizeBytes=%d, %.1f MiB, schemaVersion=%d)",
        expected_size, expected_size / _MIB, manifest_schema_version,
    )

    # Fail fast, before ever touching the DB object: a manifest whose own
    # declared schemaVersion doesn't match this server means the export is
    # simply the wrong shape for us, no download can fix that, and there's
    # no reason to pay for (or wait on) the multi-hundred-MB GET below just
    # to reject it after the fact.
    if manifest_schema_version != SCHEMA_VERSION:
        raise RuntimeError(
            f"manifest schemaVersion={manifest_schema_version!r} does not match "
            f"this server's SCHEMA_VERSION={SCHEMA_VERSION} — redeploy a matching export"
        )

    db_dir.mkdir(parents=True, exist_ok=True)
    final_path = db_dir / _LOCAL_DB_NAME
    sidecar_path = db_dir / f"{_LOCAL_DB_NAME}.sha256"
    tmp_path = db_dir / f"{_LOCAL_DB_NAME}.tmp"

    reused = (
        final_path.exists()
        and sidecar_path.exists()
        and sidecar_path.read_text().strip() == expected_sha
    )

    if reused:
        _log("local cache hit: %s matches manifest sha256; skipping download", final_path)
    else:
        _log("local cache miss: downloading (bucket=%s key=%s)", bucket, db_key)
        dl_start = time.monotonic()
        obj = client.get_object(Bucket=bucket, Key=db_key)
        _log("download start (response headers received)")
        body = obj["Body"]
        hasher = hashlib.sha256()
        size = 0
        next_progress = _PROGRESS_EVERY
        try:
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = body.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    size += len(chunk)
                    f.write(chunk)
                    if size >= next_progress:
                        pct = 100.0 * size / expected_size if expected_size else 0.0
                        _log(
                            "download progress %.0f/%.0f MiB (%.0f%%)",
                            size / _MIB, expected_size / _MIB, pct,
                        )
                        next_progress = (size // _PROGRESS_EVERY + 1) * _PROGRESS_EVERY

            dl_secs = time.monotonic() - dl_start
            _log(
                "download done (%d bytes, %.1f MiB in %.1fs, %.1f MiB/s)",
                size, size / _MIB, dl_secs, (size / _MIB) / dl_secs if dl_secs else 0.0,
            )
            # sha256 is computed incrementally while streaming; this step
            # only finalizes the digest and compares it with the manifest.
            verify_start = time.monotonic()
            _log("sha256 verify start (digest computed during download)")
            actual_sha = hasher.hexdigest()
            if actual_sha != expected_sha or size != expected_size:
                raise RuntimeError(
                    "mcp_public.db integrity check failed: "
                    f"sha256={actual_sha} (expected {expected_sha}), "
                    f"size={size} (expected {expected_size})"
                )
            _log("sha256 verify ok (%.1fs)", time.monotonic() - verify_start)
            os.replace(tmp_path, final_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    # set_db_path + assert_schema_version run whether we just downloaded or
    # reused the cached file — both paths must prove the DB on disk actually
    # matches this server's expected schema before anything queries it. If
    # this fails for ANY reason (the file's actual embedded schema_version
    # disagrees with what the manifest claimed, or is simply unreadable),
    # final_path must not survive: leaving a verified-looking file in place
    # after a failed verification would both let the server limp along
    # against bad data if something ignored the raised error, and poison the
    # next restart's sidecar-match fast-path into reusing that same bad file
    # without ever re-checking it.
    try:
        db.set_db_path(final_path)
        schema_start = time.monotonic()
        _log("schema check start")
        db.assert_schema_version()
        _log(
            "schema check ok (schema_version=%d, %.1fs)",
            SCHEMA_VERSION, time.monotonic() - schema_start,
        )
    except Exception:
        if final_path.exists():
            final_path.unlink()
        if sidecar_path.exists():
            sidecar_path.unlink()
        raise

    # Sidecar is written ONLY after a fresh download has passed every check
    # above (sha/size during streaming, schema version pre-check, and now
    # assert_schema_version against the real file) — never written on the
    # reuse path (nothing changed, sidecar already matches).
    if not reused:
        sidecar_path.write_text(expected_sha)

    _log("DB ready at %s", final_path)
    return final_path
