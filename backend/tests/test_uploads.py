"""Files a person attaches to a chat message.

An upload is the first content a caller sends that is neither prose nor a
form field: the bytes, the filename and the claimed type are all attacker
controlled, and the file is read back by a browser. These pin the four places
that matters — what may be stored, where it lands, who reads it back, and
what a reader's browser is told to do with it.
"""

import io
import struct
import zlib
from pathlib import Path

import pytest

from app import config, db
from app.services import handoff, uploads


def _png(width: int = 1, height: int = 1) -> bytes:
    """A real PNG, built here so the header is honest.

    Written by hand rather than with Pillow: a fixture the code under test
    also produced would pass whether or not the verification step runs.
    """

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _upload(client, name: str, data: bytes, mime: str = "application/octet-stream"):
    return client.post("/api/files", files={"file": (name, io.BytesIO(data), mime)})


def test_stores_an_uploaded_document(client):
    r = _upload(client, "notes.md", b"# plan\n")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "notes.md"
    assert body["mime"] == "text/markdown"
    assert body["size"] == len(b"# plan\n")


def test_the_server_names_the_file_not_the_caller(client):
    """A crafted filename must not choose a path.

    The row id names the file, so traversal has nothing to steer. The stored
    name is checked against the id rather than against a sanitized version of
    what the caller sent — sanitizing is what this must not have to rely on.
    """
    aid = _upload(client, "../../../etc/passwd.txt", b"x").json()["id"]
    row = db.query_one("SELECT path FROM artifacts WHERE id = ?", (aid,))
    stored = Path(row["path"])
    assert stored.name == f"{aid}.txt"
    assert stored.parent == Path(config.DATA_DIR) / "artifacts" / "uploads"
    assert stored.is_file()


def test_refuses_a_type_that_is_not_on_the_allowlist(client):
    r = _upload(client, "payload.svg", b"<svg onload=alert(1)>")
    assert r.status_code == 400
    # the rejected name is caller-controlled text and never comes back
    assert "payload.svg" not in r.text


def test_refuses_bytes_that_are_not_the_image_they_claim(client):
    r = _upload(client, "photo.png", b"not a png at all")
    assert r.status_code == 400


def test_the_bytes_decide_the_format_not_the_extension(client):
    """A caller controls the extension, and the format tells a provider how to
    decode the payload. Pillow reads the real one out of the header."""
    body = _upload(client, "photo.jpeg", _png()).json()
    assert body["format"] == "png"
    assert body["mime"] == "image/png"
    row = db.query_one("SELECT path FROM artifacts WHERE id = ?", (body["id"],))
    assert Path(row["path"]).suffix == ".png"


def test_refuses_a_file_over_the_cap(client):
    r = _upload(client, "big.txt", b"x" * (uploads.MAX_UPLOAD_BYTES + 1))
    assert r.status_code == 400


def test_refuses_an_empty_file(client):
    assert _upload(client, "empty.txt", b"").status_code == 400


def test_refuses_an_upload_that_would_pass_the_quota(client, monkeypatch):
    monkeypatch.setattr(uploads, "QUOTA_BYTES", 32)
    assert _upload(client, "a.txt", b"x" * 24).status_code == 200
    over = _upload(client, "b.txt", b"x" * 24)
    # a quota is not a rate cap: retrying the identical request never succeeds,
    # so it must not answer 429 and invite one (CLAUDE.md)
    assert over.status_code == 400
    assert over.headers.get("Retry-After") is None


def test_a_bidi_override_cannot_disguise_the_name(client):
    """`invoice<RLO>txt.exe` renders as `invoice.exe.txt` wherever bidi is
    honored, which is every chip this title lands in."""
    title = _upload(client, "invoice‮txt.md", b"x").json()["title"]
    assert "‮" not in title
    assert title == "invoicetxt.md"


def test_downloads_are_never_rendered_by_this_origin(client):
    aid = _upload(client, "page.html", b"<script>alert(1)</script>").json()["id"]
    r = client.get(f"/api/files/{aid}/download")
    assert r.status_code == 200
    assert r.content == b"<script>alert(1)</script>"
    # forced download plus nosniff: an uploaded .html served inline would run
    # as script on this origin, holding the reader's session
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_a_teammate_cannot_read_someone_elses_upload(client):
    aid = _upload(client, "private.md", b"secret").json()["id"]
    r = client.get(f"/api/files/{aid}/download", headers={"X-User": "mallory"})
    # 404, not 403: any other status confirms to a caller walking ids that the
    # row exists and belongs to somebody else (services/chat_threads.py)
    assert r.status_code == 404
    assert "secret" not in r.text


def test_an_upload_is_not_a_report(client):
    """Work → Reports renders every row it gets through a MARKDOWN reader, and
    read_artifact would answer read_text() on a PDF with a 500."""
    aid = _upload(client, "notes.md", b"# plan\n").json()["id"]
    assert all(a["id"] != aid for a in client.get("/api/artifacts").json())
    assert client.get(f"/api/artifacts/{aid}").status_code == 404


def test_the_upload_is_recorded_in_the_ledger(client):
    aid = _upload(client, "notes.md", b"# plan").json()["id"]
    row = db.query_one(
        "SELECT * FROM activity WHERE action = 'upload_file' ORDER BY id DESC LIMIT 1"
    )
    assert row and f"#{aid}" in row["detail"] and row["actor"] == "tester"


def test_lists_your_own_files_with_what_they_spent(client):
    """The quota is unusable without this. An upload appears on no other
    surface, so a person told they passed the limit has nothing to work from."""
    _upload(client, "a.md", b"x" * 10)
    _upload(client, "b.md", b"y" * 20)
    body = client.get("/api/files").json()
    assert [f["title"] for f in body["files"]] == ["b.md", "a.md"]  # newest first
    assert body["used"] == 30
    assert body["quota"] == uploads.QUOTA_BYTES


def test_the_list_holds_only_your_own_files(client):
    _upload(client, "mine.md", b"x")
    assert client.get("/api/files", headers={"X-User": "mallory"}).json()["files"] == []


def test_deleting_frees_the_quota_and_removes_the_file(client):
    aid = _upload(client, "notes.md", b"x" * 40).json()["id"]
    stored = Path(db.query_one("SELECT path FROM artifacts WHERE id = ?", (aid,))["path"])
    assert client.delete(f"/api/files/{aid}").status_code == 200
    assert not stored.exists()
    body = client.get("/api/files").json()
    assert body["files"] == [] and body["used"] == 0


def test_a_deleted_file_cannot_be_downloaded(client):
    aid = _upload(client, "notes.md", b"secret").json()["id"]
    client.delete(f"/api/files/{aid}")
    assert client.get(f"/api/files/{aid}/download").status_code == 404


def test_a_teammate_cannot_delete_your_file(client):
    aid = _upload(client, "notes.md", b"x").json()["id"]
    r = client.delete(f"/api/files/{aid}", headers={"X-User": "mallory"})
    # 404 like every other owner-scoped miss: another status confirms the row
    assert r.status_code == 404
    assert db.query_one("SELECT 1 FROM artifacts WHERE id = ?", (aid,))


def test_a_row_whose_file_is_already_gone_can_still_be_deleted(client):
    """A restored database beside an empty volume leaves rows holding quota
    against files that do not exist. Refusing those traps the person: the
    quota cannot be spent down and no upload succeeds again."""
    aid = _upload(client, "notes.md", b"x" * 40).json()["id"]
    Path(db.query_one("SELECT path FROM artifacts WHERE id = ?", (aid,))["path"]).unlink()
    assert client.delete(f"/api/files/{aid}").status_code == 200
    assert client.get("/api/files").json()["used"] == 0


def test_deleting_clears_a_spent_quota(client, monkeypatch):
    monkeypatch.setattr(uploads, "QUOTA_BYTES", 32)
    aid = _upload(client, "a.md", b"x" * 24).json()["id"]
    assert _upload(client, "b.md", b"y" * 24).status_code == 400
    client.delete(f"/api/files/{aid}")
    assert _upload(client, "b.md", b"y" * 24).status_code == 200


def test_the_deletion_is_recorded_in_the_ledger(client):
    aid = _upload(client, "notes.md", b"x").json()["id"]
    client.delete(f"/api/files/{aid}")
    row = db.query_one(
        "SELECT * FROM activity WHERE action = 'delete_file' ORDER BY id DESC LIMIT 1"
    )
    assert row and f"#{aid}" in row["detail"] and row["actor"] == "tester"
    # the filename never enters a ledger that cannot be edited afterwards
    assert "notes.md" not in row["detail"]


def test_no_agent_tool_can_delete_a_file(fresh_db):
    """Absent beats reviewed: no code path from a model to file destruction is
    stronger than a gated one, and nothing an agent does needs it."""
    from app.tools import ALL_TOOLS

    names = {getattr(t, "tool_name", getattr(t, "__name__", "")) for t in ALL_TOOLS}
    assert not [n for n in names if "file" in n and "delete" in n]


def test_a_stored_path_outside_the_artifact_root_is_refused(fresh_db):
    """The containment check is not about save_upload — it is about a restored
    or hand-edited row turning a stored string into a read of anything the
    server user can open."""
    aid = db.execute(
        "INSERT INTO artifacts (kind, title, path, created_by, created_at, visibility)"
        " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        ("upload", "escape", "/etc/passwd", "tester", db.now(), "private"),
    )
    row = uploads.owned_upload(aid, "tester")
    with pytest.raises(db.NotFound):
        uploads.upload_bytes(row)


def test_a_row_whose_file_is_gone_is_not_a_404(client):
    """The row is readable and the FILE is not: that is our own state — a
    missing volume — and it belongs in the error rate, not in a sentence
    telling the reader no such file exists."""
    aid = _upload(client, "notes.md", b"# plan").json()["id"]
    row = db.query_one("SELECT * FROM artifacts WHERE id = ?", (aid,))
    Path(row["path"]).unlink()
    with pytest.raises(handoff.ArtifactUnreadable):
        uploads.upload_bytes(row)
