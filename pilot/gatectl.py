"""Host-side client for the gate's control socket and run sockets (stdlib)."""
from __future__ import annotations

import http.client
import json
import socket


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path, timeout=60):
        super().__init__("localhost", timeout=timeout)
        self.path_ = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.path_)
        self.sock = s


def call(sock_path, method, path, body=None, headers=None, timeout=600):
    c = UnixHTTP(str(sock_path), timeout=timeout)
    h = {"content-type": "application/json"}
    h.update(headers or {})
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, {"raw": data[:300].decode("utf-8", "replace")}


class Gate:
    def __init__(self, ctl_sock, runs_dir):
        self.ctl, self.runs_dir = ctl_sock, runs_dir

    def health(self):
        return call(self.ctl, "GET", "/health")

    def open(self, run_id, kind="agent", arm="", task="", scenario="", caps=None):
        st, j = call(self.ctl, "POST", "/open", {"run_id": run_id, "kind": kind, "arm": arm, "task": task,
                                                  "scenario": scenario, "caps": caps or {}})
        if st != 200:
            raise RuntimeError(f"gate open failed: {st} {j}")
        return j

    def host_dir(self, run_id):
        """Host path of a run's socket directory (the gate reports its in-container path)."""
        return f"{self.runs_dir}/{run_id}"

    def close(self, run_id):
        return call(self.ctl, "POST", "/close", {"run_id": run_id})[1]

    def summary(self):
        return call(self.ctl, "GET", "/summary")[1]

    def messages(self, run_id, body, headers=None):
        return call(f"{self.runs_dir}/{run_id}/run.sock", "POST", "/v1/messages", body, headers)
