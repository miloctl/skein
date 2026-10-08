#!/usr/bin/env python3
"""TCP bridge so Docker containers can reach a loopback-only Ollama daemon.

Default Ollama installs bind 127.0.0.1:11434, which host.docker.internal can't
reach. The clean fix is a systemd override (needs root):

    sudo systemctl edit ollama    # add: Environment="OLLAMA_HOST=0.0.0.0"

Without root, run this bridge as a user service instead - it listens on the
Docker bridge gateway (172.17.0.1:11435 by default, BRIDGE_LISTEN to change
it) and forwards to 127.0.0.1:11434. Point the container at
http://host.docker.internal:11435. Stdlib only, no dependencies.

The Ollama API has no authentication and covers model management as well
as inference, so the listen address is the whole access control. Every
interface (0.0.0.0) takes BRIDGE_LISTEN_ALL=1, set on purpose.
"""

import os
import socket
import sys
import threading

LISTEN = (os.environ.get("BRIDGE_LISTEN", "172.17.0.1"), 11435)
TARGET = ("127.0.0.1", 11434)


def pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()


def main() -> None:
    if LISTEN[0] in ("0.0.0.0", "") and os.environ.get("BRIDGE_LISTEN_ALL") != "1":
        sys.exit(
            "ollama-bridge: BRIDGE_LISTEN names every interface. Set BRIDGE_LISTEN to the"
            " Docker gateway address, or set BRIDGE_LISTEN_ALL=1 on purpose."
        )
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(LISTEN)
    server.listen(64)
    print(f"ollama-bridge: {LISTEN[0]}:{LISTEN[1]} -> {TARGET[0]}:{TARGET[1]}", flush=True)
    while True:
        client, _ = server.accept()
        try:
            upstream = socket.create_connection(TARGET, timeout=10)
        except OSError:
            client.close()
            continue
        threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
        threading.Thread(target=pump, args=(upstream, client), daemon=True).start()


if __name__ == "__main__":
    main()
