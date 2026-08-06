"""Tests for advisorfinder_mcp.bootstrap's R2 download path — the branch of
ensure_db() that runs when MCP_DB_PATH is unset or points at a missing file.

No real network and no real boto3 client: every test injects a fake
S3-shaped client (get_object(Bucket, Key) -> {"Body": <has .read(n)>}). The
fake serves the REAL bytes of tests/fixtures/mcp_public.db so that
db.assert_schema_version() (which opens the file and reads export_meta)
exercises a real sqlite file rather than a stub — a fake serving b"fake"
bytes would fail with sqlite3.DatabaseError, not the RuntimeErrors this
module is actually trying to test.

Every test here repoints db.DB_PATH (via bootstrap.ensure_db) away from the
fixture DB that conftest.py's session fixture set up; the autouse
_restore_db_path fixture below points it back at teardown so later test
modules (which assume db.DB_PATH == FIXTURE_DB per conftest.py) aren't left
broken by a leaked path from a test that ran earlier in the same session —
see test_bootstrap_and_db.py's test_ensure_db_restores_after_failed_attempts
for the precedent this guards against.
"""
import hashlib
import io
import json
import os
from pathlib import Path

import pytest

from advisorfinder_mcp import SCHEMA_VERSION, bootstrap, db

FIXTURE_DB = Path(__file__).parent / "fixtures" / "mcp_public.db"
FIXTURE_BYTES = FIXTURE_DB.read_bytes()
FIXTURE_SHA = hashlib.sha256(FIXTURE_BYTES).hexdigest()
FIXTURE_SIZE = len(FIXTURE_BYTES)

R2_ENV = {
    "R2_ACCOUNT_ID": "test-account",
    "R2_ACCESS_KEY_ID": "test-key-id",
    "R2_SECRET_ACCESS_KEY": "test-secret",
}


class FakeS3Client:
    """Minimal boto3 S3 client stand-in.

    get_object(Bucket, Key) -> {"Body": <object with .read(n)>}, mirroring
    just enough of botocore's StreamingBody interface for bootstrap.py's
    chunked-read loop. Raises KeyError for any key not registered — used
    deliberately in the reuse test to prove the db object is never fetched.
    """

    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects
        self.calls: list[tuple[str, str]] = []

    def get_object(self, Bucket, Key):
        self.calls.append((Bucket, Key))
        return {"Body": io.BytesIO(self._objects[Key])}


def _manifest_bytes(sha=FIXTURE_SHA, size=FIXTURE_SIZE, schema_version=SCHEMA_VERSION):
    return json.dumps(
        {"sha256": sha, "sizeBytes": size, "schemaVersion": schema_version}
    ).encode()


@pytest.fixture(autouse=True)
def _restore_db_path():
    yield
    os.environ["MCP_DB_PATH"] = str(FIXTURE_DB)
    db.set_db_path(FIXTURE_DB)


@pytest.fixture
def r2_env(monkeypatch, tmp_path):
    """Unset MCP_DB_PATH, set the required R2 env vars, and point DB_DIR at
    a fresh tmp_path so tests never touch a real /tmp/adfi."""
    monkeypatch.delenv("MCP_DB_PATH", raising=False)
    for k, v in R2_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DB_DIR", str(tmp_path))
    return tmp_path


# ── happy path ────────────────────────────────────────────────────────────────

def test_ensure_db_downloads_verifies_and_writes_sidecar(r2_env):
    db_dir = r2_env
    client = FakeS3Client({
        "manifest.json": _manifest_bytes(),
        "mcp_public.db": FIXTURE_BYTES,
    })

    path = bootstrap.ensure_db(client=client)

    assert path == db_dir / "mcp_public.db"
    assert path.read_bytes() == FIXTURE_BYTES
    assert (db_dir / "mcp_public.db.sha256").read_text().strip() == FIXTURE_SHA
    assert not (db_dir / "mcp_public.db.tmp").exists()
    assert db.DB_PATH == path
    assert client.calls == [
        ("advisorfinder-mcp", "manifest.json"),
        ("advisorfinder-mcp", "mcp_public.db"),
    ]


def test_ensure_db_uses_custom_bucket_and_keys(r2_env, monkeypatch):
    monkeypatch.setenv("R2_BUCKET", "my-bucket")
    monkeypatch.setenv("R2_DB_KEY", "custom_db.sqlite")
    monkeypatch.setenv("R2_MANIFEST_KEY", "custom_manifest.json")
    client = FakeS3Client({
        "custom_manifest.json": _manifest_bytes(),
        "custom_db.sqlite": FIXTURE_BYTES,
    })

    bootstrap.ensure_db(client=client)

    assert client.calls == [
        ("my-bucket", "custom_manifest.json"),
        ("my-bucket", "custom_db.sqlite"),
    ]


# ── integrity failures: must not start the server, must not leave debris ──────

def test_ensure_db_sha_mismatch_raises_and_cleans_up(r2_env):
    db_dir = r2_env
    client = FakeS3Client({
        "manifest.json": _manifest_bytes(sha="0" * 64),
        "mcp_public.db": FIXTURE_BYTES,
    })

    with pytest.raises(RuntimeError, match="integrity"):
        bootstrap.ensure_db(client=client)

    assert not (db_dir / "mcp_public.db").exists()
    assert not (db_dir / "mcp_public.db.tmp").exists()
    assert not (db_dir / "mcp_public.db.sha256").exists()


def test_ensure_db_size_mismatch_raises_and_cleans_up(r2_env):
    db_dir = r2_env
    client = FakeS3Client({
        "manifest.json": _manifest_bytes(size=FIXTURE_SIZE + 1),
        "mcp_public.db": FIXTURE_BYTES,
    })

    with pytest.raises(RuntimeError, match="integrity"):
        bootstrap.ensure_db(client=client)

    assert not (db_dir / "mcp_public.db").exists()
    assert not (db_dir / "mcp_public.db.tmp").exists()


def test_ensure_db_schema_version_mismatch_raises_no_sidecar(r2_env):
    db_dir = r2_env
    client = FakeS3Client({
        "manifest.json": _manifest_bytes(schema_version=SCHEMA_VERSION + 1),
        "mcp_public.db": FIXTURE_BYTES,
    })

    with pytest.raises(RuntimeError, match="schemaVersion"):
        bootstrap.ensure_db(client=client)

    # The file itself passed sha/size verification and was replaced into
    # place — only the schemaVersion cross-check (which runs after
    # set_db_path/assert_schema_version) fails. The sidecar must NOT have
    # been written: writing it here would poison the restart fast-path,
    # letting a schema-mismatched DB reuse itself (skip verification
    # entirely) on the next boot.
    assert not (db_dir / "mcp_public.db.sha256").exists()


# ── restart fast-path: sidecar match means no re-download ──────────────────────

def test_ensure_db_reuses_when_sidecar_matches(r2_env):
    db_dir = r2_env
    final_path = db_dir / "mcp_public.db"
    final_path.write_bytes(FIXTURE_BYTES)
    (db_dir / "mcp_public.db.sha256").write_text(FIXTURE_SHA)

    # "mcp_public.db" deliberately NOT registered on the fake client — if
    # ensure_db() tries to GET the db object anyway, this raises KeyError
    # instead of silently succeeding.
    client = FakeS3Client({"manifest.json": _manifest_bytes()})

    path = bootstrap.ensure_db(client=client)

    assert path == final_path
    assert client.calls == [("advisorfinder-mcp", "manifest.json")]
    assert db.DB_PATH == final_path


def test_ensure_db_redownloads_when_sidecar_sha_differs(r2_env):
    db_dir = r2_env
    final_path = db_dir / "mcp_public.db"
    final_path.write_bytes(b"stale old bytes")
    (db_dir / "mcp_public.db.sha256").write_text("f" * 64)  # doesn't match manifest

    client = FakeS3Client({
        "manifest.json": _manifest_bytes(),
        "mcp_public.db": FIXTURE_BYTES,
    })

    path = bootstrap.ensure_db(client=client)

    assert path.read_bytes() == FIXTURE_BYTES
    assert client.calls == [
        ("advisorfinder-mcp", "manifest.json"),
        ("advisorfinder-mcp", "mcp_public.db"),
    ]


# ── env validation ──────────────────────────────────────────────────────────────

def test_ensure_db_missing_r2_env_raises(monkeypatch):
    monkeypatch.delenv("MCP_DB_PATH", raising=False)
    for k in R2_ENV:
        monkeypatch.delenv(k, raising=False)

    with pytest.raises(RuntimeError, match="R2_ACCOUNT_ID"):
        bootstrap.ensure_db()


def test_ensure_db_mcp_db_path_short_circuits_before_touching_r2(monkeypatch, r2_env):
    # r2_env's fixture deleted MCP_DB_PATH; set it back to the real fixture.
    # The short-circuit must fire before the R2 branch ever looks at
    # `client`, so passing a nonsense value for `client` must not matter.
    monkeypatch.setenv("MCP_DB_PATH", str(FIXTURE_DB))

    path = bootstrap.ensure_db(client="should never be touched")

    assert path == FIXTURE_DB
    assert db.DB_PATH == FIXTURE_DB
