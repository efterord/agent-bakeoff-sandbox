"""Isolation self-test probe, run INSIDE a sandbox launched exactly like a task.

Every probe is a harmless negative control: if a probe unexpectedly succeeds
it does nothing useful (a write into the container's own disposable layer, a
DNS lookup, a TCP connect to TEST-NET-3 203.0.113.7 which is unroutable).

    python3 -m agent.probe isolation   -> /work/out/probe.json
    python3 -m agent.probe plant       -> plants a symlink, a hard link, a FIFO and
                                          an over-limit file in /work/out for the
                                          host extractor to refuse
    python3 -m agent.probe bomb        -> writes a zip bomb as the deliverable
"""
from __future__ import annotations

import http.client
import json
import os
import re
import resource
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import forwarder  # noqa: E402

OUT = Path("/work/out")


def _try_write(path):
    try:
        with open(path, "w") as fh:
            fh.write("probe")
        return "WRITABLE"
    except OSError as e:
        return f"refused:{e.errno}"


def _status():
    d = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        k, _, v = line.partition(":")
        d[k] = v.strip()
    return d


def _cg(name):
    for base in ("/sys/fs/cgroup",):
        p = Path(base) / name
        try:
            return p.read_text().strip()
        except OSError:
            continue
    return None


def gate_call(body, headers=None, path="/v1/messages", method="POST"):
    c = http.client.HTTPConnection("127.0.0.1", forwarder.PORT, timeout=30)
    h = {"content-type": "application/json", "x-api-key": "sandbox-no-key", "anthropic-version": "2023-06-01"}
    h.update(headers or {})
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        j = json.loads(data)
    except ValueError:
        j = {"raw": data[:200].decode("utf-8", "replace")}
    return r.status, j


def isolation():
    res = {}
    st = _status()
    res["uid"], res["gid"] = os.getuid(), os.getgid()
    res["cap_eff"], res["cap_prm"], res["cap_bnd"] = st.get("CapEff"), st.get("CapPrm"), st.get("CapBnd")
    res["no_new_privs"], res["seccomp"] = st.get("NoNewPrivs"), st.get("Seccomp")
    res["rootfs_write"] = {p: _try_write(p) for p in ("/probe-root", "/etc/probe", "/usr/probe", "/opt/probe")}
    res["in_write"] = _try_write("/work/in/probe")
    res["out_write"] = _try_write("/work/out/probe-ok")
    res["tmp_write"] = _try_write("/tmp/probe-ok")
    sv = os.statvfs("/work/out")
    res["out_size_mb"] = round(sv.f_blocks * sv.f_frsize / 2**20)
    res["net_ifaces"] = sorted(os.listdir("/sys/class/net")) if os.path.isdir("/sys/class/net") else None
    try:
        socket.setdefaulttimeout(5)
        res["dns"] = "RESOLVED " + socket.gethostbyname("example.com")
    except OSError as e:
        res["dns"] = f"failed:{type(e).__name__}"
    s = socket.socket()
    s.settimeout(5)
    try:
        s.connect(("203.0.113.7", 80))
        res["tcp_testnet"] = "CONNECTED"
    except OSError as e:
        res["tcp_testnet"] = f"failed:{e.errno}"
    finally:
        s.close()
    res["pids_max"], res["memory_max"], res["cpu_max"] = _cg("pids.max"), _cg("memory.max"), _cg("cpu.max")
    res["memory_swap_max"] = _cg("memory.swap.max")
    res["rlimit_fsize"] = resource.getrlimit(resource.RLIMIT_FSIZE)
    res["rlimit_nofile"] = resource.getrlimit(resource.RLIMIT_NOFILE)
    res["rlimit_core"] = resource.getrlimit(resource.RLIMIT_CORE)
    names = sorted(os.environ)
    res["env_names"] = names
    res["env_secret_like"] = [n for n in names if re.search(r"KEY|TOKEN|SECRET|PASS|CRED", n, re.I)]
    res["env_value_keylike"] = [n for n, v in os.environ.items() if re.search(r"sk-ant-|gh[pousr]_|github_pat_", v)]
    res["docker_sock"] = os.path.exists("/var/run/docker.sock") or os.path.exists("/run/docker.sock")
    res["gate_dir"] = sorted(os.listdir("/run/gate")) if os.path.isdir("/run/gate") else None
    res["gate_dir_write"] = _try_write("/run/gate/probe")
    # ---- gate path (through the ro-mounted socket) and gate policy, all harmless
    forwarder.start()
    time.sleep(0.3)
    g = {}
    g["models"] = gate_call(None, path="/v1/models", method="GET")
    base = {"model": "claude-opus-5", "max_tokens": 99999, "temperature": 1.0,
            "messages": [{"role": "user", "content": "probe"}]}
    g["plain"] = gate_call(base, headers={"anthropic-beta": "probe-beta-2026-01-01"})
    g["server_tool"] = gate_call(dict(base, tools=[{"type": "web_search_20260209", "name": "web_search"}]))
    g["url_image"] = gate_call(dict(base, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "url", "url": "https://example.com/x.png"}}]}]))
    g["mcp"] = gate_call(dict(base, mcp_servers=[{"type": "url", "url": "https://example.com", "name": "x"}]))
    g["keylike"] = gate_call(dict(base, messages=[{"role": "user", "content": "sk-ant-" + "x" * 30}]))
    g["files_api"] = gate_call({}, path="/v1/files")
    g["batches"] = gate_call({}, path="/v1/messages/batches")
    for i in range(4):  # the self-test run is opened with max_requests=3 -> the 4th accepted call is refused
        g[f"cap_{i}"] = gate_call(base)
    res["gate"] = {k: {"status": v[0], "type": (v[1].get("error") or {}).get("type") if isinstance(v[1], dict) else None,
                       "model": v[1].get("model") if isinstance(v[1], dict) else None,
                       "msg": ((v[1].get("error") or {}).get("message") or "")[:120] if isinstance(v[1], dict) else ""}
                   for k, v in g.items()}
    (OUT / "probe.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, default=str))


def plant():
    target = OUT / "w"
    target.mkdir(exist_ok=True)
    os.symlink("/etc/passwd", OUT / "deliverable.pptx")
    (target / "real.txt").write_text("x")
    os.link(target / "real.txt", OUT / "canary.txt")
    (OUT / "trace").mkdir(exist_ok=True)
    os.mkfifo(OUT / "trace" / "events.jsonl")
    os.symlink("/proc/self/environ", OUT / "trace" / "env.jsonl")
    (OUT / "unexpected.bin").write_bytes(b"x")
    print("planted")


def bomb():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pptxbench.scan import make_zip_bomb
    make_zip_bomb(OUT / "deliverable.pptx", mb=300)
    print("bomb written", (OUT / "deliverable.pptx").stat().st_size)


if __name__ == "__main__":
    {"isolation": isolation, "plant": plant, "bomb": bomb}[sys.argv[1]]()
