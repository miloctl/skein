"""Personal remote MCP servers: rows a person registers for their own agent
turns, beside the operator's SKEIN_MCP_SERVERS list (agents/mcp_tools.py).

A personal server carries no governance block. Its tools are classified
from the server's own annotations; every write it offers needs a human
review whatever the policy engine permits, and a read runs only after one
human approved that tool once - the person adding a server must not be the
one deciding how much to trust it. The token is sealed under
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
    # resolves to nothing passes (it cannot be reached either), and the
    # connect resolves again on its own, so a name that changes its answer
    # between the two passes this check: the deployment's egress
    # NetworkPolicy is the stronger control
    # (deploy/k8s/overlays/example-prod/backend-egress.yaml is the model).
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        # ::ffff:127.0.0.1 is not loopback to the ipaddress module, and an
        # AF_INET6 connect to it reaches IPv4 loopback. A 6to4 (2002::/16) or
        # Teredo address embeds an IPv4 one the same way, and the module calls
        # 2002:a9fe:a9fe:: private, so the metadata address passed as one.
        teredo = getattr(address, "teredo", None)
        address = (
            getattr(address, "ipv4_mapped", None)
            or getattr(address, "sixtofour", None)
            or (teredo[1] if teredo else None)
            or address
        )
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or address.is_reserved
        ):
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
    check_url(url)
    # The URL is stored in plain text and travels in every backup, so a
    # credential in its query string escapes the sealed token store. Refused
    # here, not in check_url: that runs again at every connect, and a server
    # added with a query string before this rule must keep connecting.
    parts = urlsplit(url)
    if parts.query or parts.fragment:
        raise ValueError(
            "The URL must not carry a query string or a fragment. Put a credential in the"
            " token field."
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
            "INSERT INTO mcp_servers (scope, owner, name, url, auth_token_sealed,"
            " origin, created_by, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'human', ?, ?, ?) RETURNING id",
            (SCOPE, person, name, url, sealed, actor, now, now),
        )
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
    """The erasure half of the account (erasure.erase): the rows and the live
    connections. Deactivation keeps the rows through the grace."""
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
            "auth_token": credentials.unseal(row["auth_token_sealed"])
            if row["auth_token_sealed"] is not None
            else "",
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


def _active_owner(owner: str) -> None:
    from .users import is_active, is_agent

    if not is_active(owner) or is_agent(owner):
        raise ValueError("The server owner is not active. Sign in with an active account.")
