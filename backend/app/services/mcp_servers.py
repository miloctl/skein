"""Personal remote MCP servers: rows a person registers for their own agent
turns, beside the operator's SKEIN_MCP_SERVERS list (agents/mcp_tools.py).

A personal server carries no governance block. Its tools are classified
from the server's own annotations; every write it offers needs a human
review whatever the policy engine permits, and a read runs only after one
human approved that tool once — the person adding a server must not be the
one deciding how much to trust it. The token is sealed under
SKEIN_CREDENTIAL_KEY and never leaves this module unsealed except into the
connection that uses it."""

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from .. import db
from . import credentials

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
SCOPE = "personal"
# each connected server holds a background thread for the life of the
# process, and rows are otherwise unbounded per person
LIMIT = 8


def server_id(owner: str, name: str) -> str:
    """The stable id receipts and reviews key on. Changing its shape stales
    every pending remote-tool proposal (mcp_tools.execute_reviewed_mcp)."""
    return f"{SCOPE}:{owner}:{name}"


def check_url(url: str) -> None:
    """Refuse a URL that reaches this host or the cloud metadata service.
    Called at add time and again at every connect (agents/mcp_tools.py)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("The URL must start with http:// or https:// and name a host.")
    if parts.username or parts.password:
        raise ValueError(
            "The URL must not carry a user name or password. Put the token in the token field."
        )
    # SSRF: the API pod opens this URL with a POST and hands the reply to a
    # model. Loopback reaches this process and its neighbours, link-local
    # reaches the cloud metadata service. Private ranges stay allowed: an
    # MCP server on the cluster network is the main use. A host that
    # resolves to nothing passes (it cannot be reached either); the
    # deployment's egress NetworkPolicy is the stronger control
    # (deploy/k8s/overlays/example-prod/backend-egress.yaml is the model).
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        # ::ffff:127.0.0.1 is not loopback to the ipaddress module, and an
        # AF_INET6 connect to it reaches IPv4 loopback
        address = getattr(address, "ipv4_mapped", None) or address
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or address.is_reserved
        ):
            raise ValueError("The URL points at this server or its host. Name a remote MCP server.")


AUTH_MODES = ("token", "oauth")


class OAuthGrantChanged(ValueError):
    pass


def _public(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "auth": row["auth"],
        "has_token": row["auth_token_sealed"] is not None,
        "signed_in": row["oauth_tokens_sealed"] is not None,
        "oauth_signin_required": bool(row["oauth_signin_required"]),
        "server_id": server_id(row["owner"], row["name"]),
        "created_at": row["created_at"],
    }


def list_for(person: str) -> list[dict]:
    return [_public(row) for row in _rows(person)]


def _rows(person: str) -> list[dict]:
    return db.query(
        "SELECT * FROM mcp_servers WHERE scope = ? AND owner = ? ORDER BY name",
        (SCOPE, person),
    )


def add(
    person: str, name: str, url: str, token: str = "", *, auth: str = "token", actor: str
) -> dict:
    name = name.strip()
    if not _NAME.fullmatch(name):
        raise ValueError("The name must be 1 to 40 characters: lowercase letters, digits, - or _.")
    if auth not in AUTH_MODES:
        raise ValueError("The sign-in method must be token or oauth.")
    url = url.strip()
    check_url(url)
    if auth == "oauth":
        if token:
            raise ValueError("An OAuth server takes no token. Leave the token field empty.")
        if not credentials.available():
            raise ValueError(
                "OAuth tokens cannot be stored: SKEIN_CREDENTIAL_KEY is not set."
                " Whoever runs the server must set it, then add the server again."
            )
    sealed = credentials.seal(token) if token else None
    now = db.now()
    with db.transaction():
        # Deactivation holds this identity lock through credential deletion
        # (users.set_active). A registration that waited on DNS must recheck it.
        from .users import fold

        db.name_lock(db.LOCK_IDENTITY, fold(person))
        _active_owner(person)
        db.name_lock(db.LOCK_MCP_SERVER, person)
        rows = _rows(person)
        if any(row["name"] == name for row in rows):
            raise db.Conflict(
                "A server with this name already exists. Delete it, or use another name."
            )
        if len(rows) >= LIMIT:
            raise ValueError(f"You can register up to {LIMIT} servers. Delete one first.")
        sid = db.execute(
            "INSERT INTO mcp_servers (scope, owner, name, url, auth, auth_token_sealed,"
            " origin, created_by, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'human', ?, ?, ?) RETURNING id",
            (SCOPE, person, name, url, auth, sealed, actor, now, now),
        )
        db.log_activity(actor, "add_mcp_server", f"#{sid} {name}")
    return _public(
        {
            "id": sid,
            "owner": person,
            "name": name,
            "url": url,
            "auth": auth,
            "auth_token_sealed": sealed,
            "oauth_tokens_sealed": None,
            "oauth_signin_required": False,
            "created_at": now,
        }
    )


def delete(sid: int, person: str, *, actor: str) -> dict:
    from ..agents.mcp_tools import forget

    with db.transaction():
        row = db.query_one(
            "DELETE FROM mcp_servers WHERE id = ? AND scope = ? AND owner = ? RETURNING name",
            (sid, SCOPE, person),
        )
        if row is None:
            raise db.NotFound(f"server #{sid} not found (or not yours)")
        db.log_activity(actor, "delete_mcp_server", f"#{sid} {row['name']}")
    forget(server_id(person, row["name"]))
    return {"id": sid, "deleted": True}


def delete_for(person: str, *, actor: str = "system") -> int:
    """The offboarding half of users.set_active(False), beside revoke_keys_for."""
    from ..agents.mcp_tools import forget_owner

    rows = db.query(
        "DELETE FROM mcp_servers WHERE scope = ? AND owner = ? RETURNING name", (SCOPE, person)
    )
    forget_owner(person)
    if rows:
        db.log_activity(actor, "delete_mcp_servers_for", f"{person}: {len(rows)} server(s)")
    return len(rows)


def _stamp(row_id: int, updated_at: str) -> str:
    return f"{row_id}:{updated_at}"


def _entry(row: dict) -> tuple[str, dict]:
    sid = server_id(row["owner"], row["name"])
    return (
        sid,
        {
            "id": row["id"],
            "owner": row["owner"],
            "server_id": sid,
            "name": row["name"],
            "url": row["url"],
            "auth": row["auth"],
            "auth_token": credentials.unseal(row["auth_token_sealed"])
            if row["auth_token_sealed"] is not None
            else "",
            "signed_in": row["oauth_tokens_sealed"] is not None,
            "signin_required": bool(row["oauth_signin_required"]),
            "oauth_redirect_uri": row["oauth_redirect_uri"],
            "derive": True,
            "tier": SCOPE,
            # the row id as well as updated_at, which has one-second
            # resolution: a delete and re-add of one name within a second
            # keeps the time, and every stamp check in agents/mcp_tools.py
            # would keep the deleted server's connection and credential
            "stamp": _stamp(row["id"], row["updated_at"]),
        },
    )


def entries_for(person: str) -> list[tuple[str, dict]]:
    """Server entries in the shape mcp_tools consumes, token unsealed."""
    return [_entry(row) for row in _rows(person)]


def entry_for(sid: int, person: str) -> tuple[str, dict]:
    row = db.query_one(
        "SELECT * FROM mcp_servers WHERE id = ? AND scope = ? AND owner = ?", (sid, SCOPE, person)
    )
    if row is None:
        raise db.NotFound(f"server #{sid} not found (or not yours)")
    return _entry(row)


def mark_oauth_sign_in(sid: int, required: bool) -> None:
    db.execute("UPDATE mcp_servers SET oauth_signin_required = ? WHERE id = ?", (required, sid))


def load_oauth(sid: int) -> tuple[str, str]:
    """(tokens JSON, client JSON), each '' when absent or sealed under a
    key that changed — then the next sign-in replaces it."""
    row = db.query_one(
        "SELECT oauth_tokens_sealed, oauth_client_sealed FROM mcp_servers WHERE id = ?", (sid,)
    )
    if row is None:
        return "", ""
    return tuple(  # type: ignore[return-value]
        credentials.unseal(row[column]) if row[column] is not None else ""
        for column in ("oauth_tokens_sealed", "oauth_client_sealed")
    )


def _active_owner(owner: str) -> None:
    from .users import is_active, is_agent

    if not is_active(owner) or is_agent(owner):
        raise ValueError("The server owner is not active. Sign in with an active account.")


def _owned_oauth(sid: int, owner: str) -> None:
    from .users import fold

    db.name_lock(db.LOCK_IDENTITY, fold(owner))
    _active_owner(owner)
    if not db.query_one(
        "SELECT 1 FROM mcp_servers WHERE id = ? AND owner = ? AND auth = 'oauth' FOR UPDATE",
        (sid, owner),
    ):
        raise ValueError("The OAuth server is not available. Start sign-in again from Settings.")


def claim_oauth(
    sid: int, owner: str, seconds: float, *, redirect_uri: str = "", browser: str = ""
) -> str:
    """Own discovery, registration and token storage, not only the callback wait."""
    claim = secrets.token_urlsafe(32)
    with db.transaction():
        _owned_oauth(sid, owner)
        db.execute(
            "DELETE FROM mcp_oauth_flows WHERE server_id = ? AND expires_at <= ?", (sid, db.now())
        )
        expires = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(
            timespec="microseconds"
        )
        if not db.execute_rowcount(
            "INSERT INTO mcp_oauth_flows"
            " (claim_id, server_id, owner, created_at, expires_at, browser_binding)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (server_id) DO NOTHING",
            (claim, sid, owner, db.now(), expires, _browser_hash(browser) if browser else None),
        ):
            raise ValueError("A sign-in for this server is already in progress. Finish it first.")
        if redirect_uri:
            db.execute(
                "UPDATE mcp_servers SET oauth_redirect_uri = ? WHERE id = ?", (redirect_uri, sid)
            )
    return claim


def _browser_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def oauth_browser_matches(state: str, candidates: list[str]) -> bool:
    """Whether the browser at the callback is the one that started the flow.

    The state alone names the flow, and it travels in the authorization URL.
    Without this, a person who sends that URL to a colleague receives the
    colleague's grant on their own server, and then acts as the colleague on
    the remote service."""
    row = db.query_one(
        "SELECT browser_binding FROM mcp_oauth_flows WHERE state = ? AND expires_at > ?",
        (state, db.now()),
    )
    stored = (row or {}).get("browser_binding") or ""
    return bool(stored) and any(
        hmac.compare_digest(stored, _browser_hash(value)) for value in candidates if value
    )


def release_oauth(claim: str) -> None:
    db.execute("DELETE FROM mcp_oauth_flows WHERE claim_id = ?", (claim,))


def prune_oauth_flows() -> int:
    return db.execute_rowcount("DELETE FROM mcp_oauth_flows WHERE expires_at <= ?", (db.now(),))


def register_oauth_state(claim: str, state: str) -> None:
    if not state or not db.execute_rowcount(
        "UPDATE mcp_oauth_flows SET state = ?"
        " WHERE claim_id = ? AND state IS NULL AND expires_at > ?",
        (state, claim, db.now()),
    ):
        raise ValueError("The sign-in expired. Start it again from Settings.")


_CALLBACK_PREFIX = b"skein-oauth-callback-v1:"


def _seal_callback(code: str, iss: str | None) -> bytes:
    return _CALLBACK_PREFIX + credentials.seal(json.dumps({"code": code, "iss": iss}))


def _unseal_callback(sealed: bytes) -> dict:
    blob = bytes(sealed)
    if not blob.startswith(_CALLBACK_PREFIX):
        # Old callbacks seal an opaque code. Parsing that code as JSON could
        # invent an issuer the authorization server never sent.
        return {"code": credentials.unseal(blob), "iss": None}
    try:
        value = json.loads(credentials.unseal(blob[len(_CALLBACK_PREFIX) :]))
    except ValueError:
        return {"code": "", "iss": None}
    if (
        not isinstance(value, dict)
        or set(value) != {"code", "iss"}
        or not isinstance(value["code"], str)
        or (value["iss"] is not None and not isinstance(value["iss"], str))
    ):
        return {"code": "", "iss": None}
    return value


def complete_oauth(state: str, code: str, refused: bool, *, iss: str | None = None) -> bool:
    if not credentials.available():
        return False
    return bool(
        db.execute_rowcount(
            "UPDATE mcp_oauth_flows f SET code_sealed = ?, refused = ?, done = TRUE"
            " WHERE state = ? AND NOT done AND expires_at > ?"
            " AND EXISTS (SELECT 1 FROM mcp_servers s"
            " WHERE s.id = f.server_id AND s.owner = f.owner)",
            (_seal_callback(code, iss) if code and not refused else None, refused, state, db.now()),
        )
    )


def oauth_result(claim: str) -> dict | None:
    row = db.query_one(
        "SELECT f.code_sealed, f.refused, f.done FROM mcp_oauth_flows f"
        " JOIN mcp_servers s ON s.id = f.server_id AND s.owner = f.owner"
        " WHERE f.claim_id = ? AND f.expires_at > ?",
        (claim, db.now()),
    )
    if row:
        sealed = row.pop("code_sealed")
        row.update(_unseal_callback(sealed) if sealed else {"code": "", "iss": None})
    return row


def store_oauth(
    sid: int,
    owner: str,
    expected: tuple[str, str],
    *,
    claim: str = "",
    tokens: str = "",
    client: str = "",
) -> str:
    """A refresh must not overwrite a newer grant from another process.

    Returns the row's new stamp after an interactive (claimed) grant, else
    "". A sign-in changes the stamp so every other process drops its
    connection and reconnects with the new tokens: kept, a connection there
    would refresh with its old grant, fail, and mark the server signed out
    again (agents/mcp_oauth.py redirect)."""
    with db.transaction():
        _owned_oauth(sid, owner)
        active = db.query_one(
            "SELECT claim_id, owner FROM mcp_oauth_flows WHERE server_id = ? AND expires_at > ?",
            (sid, db.now()),
        )
        if (
            (claim and (not active or active != {"claim_id": claim, "owner": owner}))
            or (not claim and active)
            or load_oauth(sid) != expected
        ):
            raise OAuthGrantChanged("The sign-in changed. Start it again from Settings.")
        # Only an interactive, claimed grant can replace registration. A live
        # connection in another process can still try to refresh the old client.
        if client and not claim:
            raise ValueError("A sign-in is required. Start it from Settings.")
        stamp = db.now() if tokens and claim else ""
        if tokens:
            db.execute(
                "UPDATE mcp_servers SET oauth_tokens_sealed = ?, oauth_signin_required = FALSE,"
                " updated_at = COALESCE(NULLIF(?, ''), updated_at) WHERE id = ?",
                (credentials.seal(tokens), stamp, sid),
            )
        if client:
            db.execute(
                "UPDATE mcp_servers SET oauth_client_sealed = ? WHERE id = ?",
                (credentials.seal(client), sid),
            )
        return _stamp(sid, stamp) if stamp else ""
