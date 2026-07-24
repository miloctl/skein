#!/usr/bin/env python3
"""TCP bridge so Docker containers can reach a loopback-only Ollama daemon.

Default Ollama installs bind 127.0.0.1:11434, which host.docker.internal can't
reach. The clean fix is a systemd override (needs root):

    sudo systemctl edit ollama    # add: Environment="OLLAMA_HOST=0.0.0.0"

Without root, run this bridge as a user service instead — it listens on
0.0.0.0:11435 and forwards to 127.0.0.1:11434. Point the container at
http://host.docker.internal:11435. Stdlib only, no dependencies.
"""

import socket
import threading

LISTEN = ("0.0.0.0", 11435)
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
