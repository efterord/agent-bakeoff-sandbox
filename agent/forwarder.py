"""In-container TCP -> unix-socket forwarder (stdlib only).

The agent container has --network none (loopback only). Agents speak HTTP to
127.0.0.1:8088; every connection is piped to the per-run gate socket
/run/gate/run.sock, the only IPC mount. The gate attributes requests by the
socket they arrive on, never by anything the sandbox sends.
"""
import os
import socket
import sys
import threading

SOCK = os.environ.get("PILOT_GATE_SOCK", "/run/gate/run.sock")
PORT = int(os.environ.get("PILOT_GATE_PORT", "8088"))


def _pipe(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _serve(client):
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(SOCK)
    except OSError:
        client.close()
        return
    threading.Thread(target=_pipe, args=(client, up), daemon=True).start()
    _pipe(up, client)
    client.close()
    up.close()


def start(port: int = PORT) -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(32)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=_serve, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return srv


if __name__ == "__main__":
    start()
    print(f"forwarder 127.0.0.1:{PORT} -> {SOCK}", flush=True)
    threading.Event().wait()
