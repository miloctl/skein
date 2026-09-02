"""Personal remote MCP servers: rows a person registers for their own agent
turns, beside the operator's SKEIN_MCP_SERVERS list (agents/mcp_tools.py).

A personal server carries no governance block. Its tools are classified
from the server's own annotations, and every write it offers needs a human
review whatever the policy engine permits — the person adding a server must
not be the one deciding how much to trust it. The token is sealed under
SKEIN_CREDENTIAL_KEY and never leaves this module unsealed except into the
connection that uses it."""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

from .. import db
from . import credentials

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
SCOPE = "personal"


def server_id(owner: str, name: str) -> str:
    """The stable id receipts and reviews key on. Changing its shape stales
    every pending remote-tool proposal (mcp_tools.execute_reviewed_mcp)."""
    return f"{SCOPE}:{owner}:{name}"


def _check_url(url: str) -> None:
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
    # MCP server on the cluster network is the main use. The check runs at
    # add time only, so a host that later resolves elsewhere is not caught
    # here — the deployment's egress NetworkPolicy is the stronger control
    # when one exists (none in deploy/k8s/base today).
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_loopback or address.is_link_local or address.is_unspecified:
            raise ValueError("The URL points at this server or its host. Name a remote MCP server.")


def _public(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "has_token": row["auth_token_sealed"] is not None,
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


def add(person: str, name: str, url: str, token: str = "", *, actor: str) -> dict:
    name = name.strip()
    if not _NAME.fullmatch(name):
        raise ValueError("The name must be 1 to 40 characters: lowercase letters, digits, - or _.")
    url = url.strip()
    _check_url(url)
    sealed = credentials.seal(token) if token else None
    now = db.now()
    with db.transaction():
        try:
            with db.savepoint():
                sid = db.execute(
                    "INSERT INTO mcp_servers (scope, owner, name, url, auth_token_sealed,"
                    " origin, created_by, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 'human', ?, ?, ?) RETURNING id",
                    (SCOPE, person, name, url, sealed, actor, now, now),
                )
        except db.UniqueViolation:
            raise db.Conflict(
                "A server with this name already exists. Delete it, or use another name."
            ) from None
        db.log_activity(actor, "add_mcp_server", f"#{sid} {name}")
    return _public(
        {
            "id": sid,
            "owner": person,
            "name": name,
            "url": url,
            "auth_token_sealed": sealed,
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
    from ..agents.mcp_tools import forget

    rows = db.query(
        "DELETE FROM mcp_servers WHERE scope = ? AND owner = ? RETURNING name", (SCOPE, person)
    )
    for row in rows:
        forget(server_id(person, row["name"]))
    if rows:
        db.log_activity(actor, "delete_mcp_servers_for", f"{person}: {len(rows)} server(s)")
    return len(rows)


def entries_for(person: str) -> list[tuple[str, dict]]:
    """Server entries in the shape mcp_tools consumes, token unsealed."""
    return [
        (
            server_id(person, row["name"]),
            {
                "name": row["name"],
                "url": row["url"],
                "auth_token": credentials.unseal(row["auth_token_sealed"])
                if row["auth_token_sealed"] is not None
                else "",
                "derive": True,
                "tier": SCOPE,
                "stamp": row["updated_at"],
            },
        )
        for row in _rows(person)
    ]
