import json
import runpy
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "stub-idp.py"


def test_an_explicit_group_map_fails_closed_for_unknown_users(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["stub-idp.py", "8610", "skein", '{"mira":["atlas-delivery-managers"]}'],
    )
    groups_for = runpy.run_path(str(SCRIPT))["groups_for"]
    assert groups_for("mira") == ["atlas-delivery-managers"]
    assert groups_for("typo") == []


def test_an_explicit_empty_group_map_grants_no_groups(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["stub-idp.py", "8610", "skein", "{}"])
    groups_for = runpy.run_path(str(SCRIPT))["groups_for"]
    assert groups_for("ava") == []


def test_the_default_browser_walk_keeps_its_admin_group(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["stub-idp.py", "8610", "skein"])
    groups_for = runpy.run_path(str(SCRIPT))["groups_for"]
    assert groups_for("ava") == ["skein-admins"]


def test_a_slow_browser_connection_does_not_block_identity_requests():
    # The command uses sys.executable and a fixed repository script. No input
    # selects the executable.
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, str(SCRIPT), "0", "skein"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    slow = None
    try:
        assert process.stdout
        issuer = process.stdout.readline().split(" on ", 1)[1].split(" ", 1)[0]
        port = urllib.parse.urlparse(issuer).port
        assert port is not None
        deadline = time.monotonic() + 2
        while slow is None:
            try:
                slow = socket.create_connection(("127.0.0.1", port), timeout=0.1)
            except ConnectionRefusedError:
                assert process.poll() is None
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        slow.sendall(b"GET /authorize HTTP/1.1\r\nHost: 127.0.0.1\r\n")
        time.sleep(0.05)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/jwks", timeout=1) as response:
            assert json.load(response)["keys"]
    finally:
        if slow is not None:
            slow.close()
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
