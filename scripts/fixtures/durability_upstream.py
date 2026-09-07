"""Local effect recorder, OAuth MCP server, HTTP router and per-pod DB links."""

# ruff: noqa: S101, S104 — test assertions and container-internal listeners
import http.client
import json
import os
import select
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

LOCK = threading.Condition()
STATE = {
    "events": [],
    "released": [],
    "before_commit": False,
    "drop_ack": False,
    "routes": [],
    "db_a": True,
    "db_b": True,
}
BASE = "http://fixture:8080"


def event(kind, value):
    with LOCK:
        STATE["events"].append({"kind": kind, "value": value})
        LOCK.notify_all()


class DatabaseLink(socketserver.BaseRequestHandler):
    def handle(self):
        name = self.server.link_name
        if not STATE[name]:
            return
        try:
            with socket.create_connection(
                (os.environ["DURABILITY_DB"], 5432), timeout=2
            ) as upstream:
                peers = (self.request, upstream)
                while STATE[name]:
                    readable, _, _ = select.select(peers, [], [], 0.2)
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.request else self.request).sendall(data)
        except OSError:
            return


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def reply(self, status, value, headers=None):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def proxy(self, body=b""):
        with LOCK:
            routes = STATE["routes"]
            if not routes:
                return self.reply(503, {"detail": "No test backend is ready."})
            route = routes.pop(0)
            routes.append(route)
            drop = STATE["drop_ack"] and self.path == "/api/webhooks/forge"
            if drop:
                STATE["drop_ack"] = False
        conn = http.client.HTTPConnection(route, 8000, timeout=20)
        try:
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in {"host", "connection"}
            }
            conn.request(self.command, self.path, body, headers)
            response = conn.getresponse()
            data = response.read()
            if drop:
                event("dropped_ack", {"status": response.status})
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self.reply(503, {"detail": "The test backend is unavailable."})
        finally:
            conn.close()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/state":
            with LOCK:
                return self.reply(200, STATE)
        if path.startswith("/.well-known/oauth-protected-resource"):
            return self.reply(200, {"resource": BASE + "/mcp", "authorization_servers": [BASE]})
        if path.startswith("/.well-known/oauth-authorization-server") or path.startswith(
            "/.well-known/openid-configuration"
        ):
            return self.reply(
                200,
                {
                    "issuer": BASE,
                    "authorization_endpoint": BASE + "/authorize",
                    "token_endpoint": BASE + "/token",
                    "registration_endpoint": BASE + "/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        if path == "/mcp":
            return self.reply(405, {})
        return self.proxy()

    def do_POST(self):
        body = self.body()
        path = urlsplit(self.path).path
        if path == "/control":
            changes = json.loads(body)
            assert set(changes) <= set(STATE) - {"events"}
            with LOCK:
                STATE.update(changes)
                LOCK.notify_all()
            return self.reply(200, {"ok": True})
        if path.startswith("/gate/"):
            task = path.rsplit("/", 1)[1]
            event("gate", {"task": task, **json.loads(body)})
            with LOCK:
                released = LOCK.wait_for(lambda: task in STATE["released"], timeout=230)
            return self.reply(200 if released else 504, {"released": released})
        if path == "/before-commit":
            event("before_commit", json.loads(body))
            with LOCK:
                LOCK.wait_for(lambda: not STATE["before_commit"], timeout=230)
            return self.reply(200, {"ok": True})
        if path.startswith(("/observed/", "/effect/")):
            event(path.strip("/"), json.loads(body))
            return self.reply(200, {"recorded": True})
        if path == "/register":
            return self.reply(
                201,
                {
                    **json.loads(body),
                    "client_id": "durability-client",
                    "token_endpoint_auth_method": "none",
                },
            )
        if path == "/token":
            values = parse_qs(body.decode())
            if values.get("code") != ["durability-code"]:
                return self.reply(400, {"error": "invalid_grant"})
            event("oauth_token", {"code": "redeemed"})
            return self.reply(
                200,
                {"access_token": "durability-access", "token_type": "Bearer", "expires_in": 3600},
            )
        if path == "/mcp":
            if self.headers.get("Authorization") != "Bearer durability-access":
                return self.reply(
                    401,
                    {},
                    {
                        "WWW-Authenticate": f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource"'
                    },
                )
            value = json.loads(body)
            if "id" not in value:
                return self.reply(202, {})
            method = value.get("method")
            result = (
                {"tools": []}
                if method == "tools/list"
                else {
                    "protocolVersion": value.get("params", {}).get("protocolVersion", "2025-03-26"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "durability-fixture", "version": "1.0"},
                }
            )
            event("mcp", {"method": method})
            return self.reply(200, {"jsonrpc": "2.0", "id": value["id"], "result": result})
        return self.proxy(body)

    def do_DELETE(self):
        return self.proxy(self.body())

    def do_PATCH(self):
        return self.proxy(self.body())


if __name__ == "__main__":
    for port, name in ((15432, "db_a"), (15433, "db_b")):
        server = socketserver.ThreadingTCPServer(("0.0.0.0", port), DatabaseLink)
        server.daemon_threads = True
        server.link_name = name
        threading.Thread(target=server.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
