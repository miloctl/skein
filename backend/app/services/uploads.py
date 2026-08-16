"""Files a person attaches to a chat message.

Stored as `artifacts` rows so uploads inherit the containment root under
data/artifacts, the provenance columns, and the sinks that already skip the
private tier (search.index_record, admin.export, every WORKSPACE_ONLY job).

Ownership follows CHAT, not the visibility tiers: `services/chat_threads.py`
scopes a thread by an owner column against the resolved name, which works in
every auth mode, and an attachment belongs to the conversation it was typed
into. The row still carries the private tier so the sinks above skip it, but
no read here goes through scope.visible_filter — that filter drops the author
arm for a nameless viewer, so in trusted-header mode (the default) a private
upload would be unreadable by the person who just made it.
"""

import io
import unicodedata
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .. import config, db
from . import handoff

# Exactly the formats strands' ContentBlock accepts (strands/types/media.py),
# so nothing reaches a provider that the provider cannot parse. SVG is absent
# ON PURPOSE and must stay absent: it carries script, and it is the one image
# type that would put the exfiltration channel components/thread.tsx closes
# back into a surface that renders it.
DOCUMENTS = {
    "pdf": "application/pdf",
    "csv": "text/csv",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html",
    "txt": "text/plain",
    "md": "text/markdown",
}
IMAGES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
# what a person types, mapped to the format name the model API uses
ALIASES = {"jpg": "jpeg", "htm": "html", "markdown": "md", "text": "txt"}

# A document a person attaches, not a report our own generator wrote, so this
# is its own number rather than handoff.MAX_ARTIFACT_BYTES. The whole file is
# held in memory twice on the way through (the request body, then the model
# payload), and one worker serves the request.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# Per person, across every upload they still own. Deleting is what frees it.
QUOTA_BYTES = 100 * 1024 * 1024
TITLE_LIMIT = 120


def _extension(filename: str) -> str:
    ext = Path(filename).suffix.lstrip(".").lower()
    return ALIASES.get(ext, ext)


def safe_title(filename: str) -> str:
    """The client filename, reduced to something safe to SHOW.

    It never reaches the filesystem — save_upload names the file after the row
    id — so this is not a traversal guard. It is a spoofing guard: the name
    renders in a chip beside a teammate's message, and a right-to-left
    override turns `invoice<U+202E>txt.exe` into something that reads as
    `invoice.exe.txt` in every UI that honors bidi. Cc/Cf covers the override
    characters and the C0/C1 controls in one test.
    """
    name = Path(filename).name
    cleaned = "".join(c for c in name if unicodedata.category(c) not in ("Cc", "Cf"))
    cleaned = cleaned.strip().strip(".")
    return cleaned[:TITLE_LIMIT] or "file"


def _verified_image_format(data: bytes, claimed: str) -> str:
    """The format Pillow reads out of the bytes, not the one the name claims.

    A caller controls the extension, and the format is what tells the provider
    how to decode the payload. Image.open reads the HEADER only, so the
    decompression-bomb guard (Pillow's own MAX_IMAGE_PIXELS, left at its
    default on purpose) refuses a small file that declares a huge canvas
    before any bitmap is allocated.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
            found = (img.format or "").lower()
    except Image.DecompressionBombError as e:
        raise ValueError(
            "the image declares too many pixels to open. Attach a smaller image."
        ) from e
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ValueError("the file is not a readable image. Attach a valid image file.") from e
    found = ALIASES.get(found, found)
    if found not in IMAGES:
        raise ValueError(f"an image must be one of {', '.join(sorted(IMAGES))}.")
    # The NAME loses to the bytes, so `claimed` is deliberately unused past
    # this point: a .png holding a gif is stored and served as a gif, and the
    # extension a caller chose never decides the stored Content-Type.
    return found


def used_bytes(owner: str) -> int:
    row = db.query_one(
        "SELECT COALESCE(SUM(size), 0) AS used FROM artifacts"
        " WHERE kind = 'upload' AND created_by = ?",
        (owner,),
    )
    return int(row["used"]) if row else 0


def save_upload(filename: str, data: bytes, *, owner: str) -> dict:
    """Store one uploaded file and return its row.

    `data` is already bounded by the route, which stops reading the body past
    MAX_UPLOAD_BYTES rather than trusting a Content-Length header.
    """
    ext = _extension(filename)
    if ext not in IMAGES and ext not in DOCUMENTS:
        # the rejected name is never echoed: it is caller-controlled text that
        # would land in a log and in a teammate's error surface (CLAUDE.md)
        raise ValueError(
            "that file type cannot be attached. Attach one of: "
            f"{', '.join(sorted(DOCUMENTS) + sorted(IMAGES))}."
        )
    if not data:
        raise ValueError("the file is empty. Attach a file with content in it.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"the file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            " Attach a smaller file."
        )
    if ext in IMAGES:
        ext = _verified_image_format(data, ext)
        mime = IMAGES[ext]
    else:
        mime = DOCUMENTS[ext]

    root = Path(config.DATA_DIR) / "artifacts" / "uploads"
    root.mkdir(parents=True, exist_ok=True)

    with db.transaction():
        # FIRST in the transaction, and before the quota read: the quota
        # decides whether this insert happens, and a read holds nothing on its
        # own, so two uploads racing each other both saw room and both took it.
        db.name_lock(db.LOCK_UPLOAD, owner)
        used = used_bytes(owner)
        if used + len(data) > QUOTA_BYTES:
            raise ValueError(
                f"your uploads would pass the {QUOTA_BYTES // (1024 * 1024)} MB limit."
                " Open Settings, then delete an attached file."
            )
        title = safe_title(filename)
        # The row is inserted before the file exists, because the FILE IS
        # NAMED AFTER THE ROW ID — a server-generated name is what makes a
        # crafted filename unable to choose a path. The path column is filled
        # in the same transaction, so a crash between the two rolls the row
        # back rather than leaving one that points nowhere.
        aid = db.execute(
            "INSERT INTO artifacts (kind, title, path, created_by, created_at,"
            " visibility, mime, size) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            ("upload", title, "", owner, db.now(), "private", mime, len(data)),
        )
        path = root / f"{aid}.{ext}"
        db.execute("UPDATE artifacts SET path = ? WHERE id = ?", (str(path), aid))
        path.write_bytes(data)
        db.log_activity(owner, "upload_file", f"artifact #{aid} ({len(data)} bytes)")
        return {
            "id": aid,
            "title": title,
            "mime": mime,
            "size": len(data),
            "format": ext,
            "media": "image" if ext in IMAGES else "document",
        }


def owned_upload(artifact_id: int, owner: str) -> dict:
    """One upload row, for the person who made it.

    Owner-scoped like a chat thread, and a miss is NotFound for the same
    reason chat_threads gives: any other status tells a caller walking ids
    that the row exists and belongs to somebody else.
    """
    row = db.query_one(
        "SELECT * FROM artifacts WHERE id = ? AND kind = 'upload' AND created_by = ?",
        (artifact_id, owner),
    )
    if not row:
        raise db.NotFound(f"no attached file #{artifact_id} for {owner}")
    return row


def _contained_path(row: dict) -> Path:
    """The file for an upload row, refused if it escapes the artifact root.

    `path` is a stored string, and every caller turns one into a file
    operation. Every writer is save_upload above, so the check is not about
    them — it is about a restored or hand-edited row. resolve() runs BEFORE
    the containment test so a symlink planted under the directory is followed
    to its target and then refused.
    """
    root = (Path(config.DATA_DIR) / "artifacts").resolve()
    try:
        path = Path(row["path"]).resolve()
    except ValueError as e:
        # a NUL byte in the stored path — pathlib's own message would cross the
        # API boundary as our error text
        raise db.NotFound(f"no attached file #{row['id']}") from e
    if not path.is_relative_to(root):
        raise db.NotFound(f"no attached file #{row['id']}")
    return path


def upload_bytes(row: dict) -> bytes:
    """The stored file, with the same containment the artifact reader applies."""
    path = _contained_path(row)
    # Past the containment check it is OUR state, never something a caller
    # sent, so it stays a 500 and shows up in the error rate — the same split
    # handoff.read_artifact makes. is_file() is False for a FIFO as well as
    # for an absent path, and read_bytes on a FIFO blocks this worker for good.
    if not path.is_file():
        raise handoff.ArtifactUnreadable(
            f"attached file #{row['id']} has no file on disk."
            " Check that the volume holding data/artifacts is mounted."
        )
    return path.read_bytes()


def list_uploads(owner: str) -> dict:
    """This person's own attached files, and what they have spent.

    The quota is unusable without this. An upload is private, so it appears on
    no other surface — a person told "your uploads would pass the limit" with
    no list to work from cannot act on the sentence at all.
    """
    rows = db.query(
        "SELECT id, title, mime, size, created_at FROM artifacts"
        " WHERE kind = 'upload' AND created_by = ? ORDER BY id DESC",
        (owner,),
    )
    return {
        "files": rows,
        "used": sum(int(r["size"]) for r in rows),
        "quota": QUOTA_BYTES,
        "max_file": MAX_UPLOAD_BYTES,
    }


def delete_upload(artifact_id: int, owner: str) -> dict:
    """Delete one of your own attached files, and free the quota it held.

    A human deleting their own private file, so it is a plain owner-scoped
    REST delete rather than a reviewed one — the shape services/chat_threads.py
    already uses for deleting a chat, which destroys strictly more. A review
    here would ask a teammate to approve destroying something they cannot
    read, and the proposal row would announce that the file exists.

    There is deliberately no agent tool for this. Absent beats reviewed: no
    code path from a model to file destruction is stronger than a gated one,
    and nothing an agent does needs it.
    """
    with db.transaction():
        # the same lock save_upload takes, in the same order: this changes
        # what used_bytes returns, so an upload racing a delete must not read
        # the quota mid-change
        db.name_lock(db.LOCK_UPLOAD, owner)
        row = owned_upload(artifact_id, owner)
        path = _contained_path(row)
        db.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
        # missing_ok: a row whose file is already gone (a restored database
        # beside an empty volume) is exactly the row somebody needs to delete
        # to free a stuck quota. Refusing it there would trap them.
        path.unlink(missing_ok=True)
        db.log_activity(owner, "delete_file", f"artifact #{artifact_id} ({row['size']} bytes)")
        return {"id": artifact_id, "deleted": True}
