"""Host-side sandbox launcher (GitHub-hosted runner only). One fresh container
per task, identical for every arm except the entrypoint:

  --network none --read-only (+ tmpfs /tmp, /home/agent) --user 10001:10001
  --cap-drop ALL --security-opt no-new-privileges=true (default seccomp/AppArmor)
  --pids-limit 256 --memory 3g --memory-swap 3g --cpus 1.5 --ipc private
  --ulimit nofile=1024 fsize=256MiB core=0, --init, 15-minute wall clock,
  mounts: in (tmpfs, read-only), out (128 MiB tmpfs), per-run gate socket dir (read-only)
  no env except HOME and an opaque run id; docker rm -f afterwards.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

UID = 10001
WALL_S = 900


def sh(argv, check=True, **kw):
    return subprocess.run(argv, check=check, capture_output=True, text=True, **kw)


def prepare(tdir: Path, files: dict[str, Path | bytes]) -> tuple[Path, Path]:
    """tmpfs in (filled, then remounted read-only) and tmpfs out (128 MiB, owned by the sandbox uid)."""
    i, o = tdir / "in", tdir / "out"
    i.mkdir(parents=True, exist_ok=True)
    o.mkdir(parents=True, exist_ok=True)
    sh(["sudo", "mount", "-t", "tmpfs", "-o", "size=32m,mode=0755,nosuid,nodev,noexec", "tmpfs", str(i)])
    sh(["sudo", "chown", f"{os.getuid()}:{os.getgid()}", str(i)])
    for name, src in files.items():
        dst = i / name
        if isinstance(src, (bytes, bytearray)):
            dst.write_bytes(src)
        else:
            shutil.copyfile(src, dst)
        os.chmod(dst, 0o644)
    sh(["sudo", "mount", "-o", "remount,ro", str(i)])
    sh(["sudo", "mount", "-t", "tmpfs", "-o", f"size=128m,mode=0700,uid={UID},gid={UID},nosuid,nodev,noexec",
        "tmpfs", str(o)])
    return i, o


def run(name: str, image: str, entry: list[str], in_dir: Path, out_dir: Path, gate_dir: Path | None,
        wall_s: int = WALL_S, run_id: str = "", extra_env: dict | None = None) -> dict:
    argv = ["docker", "run", "-d", "--name", name,
            "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
            "--tmpfs", f"/home/agent:rw,nosuid,nodev,size=512m,uid={UID},gid={UID},mode=0700",
            "--mount", f"type=bind,src={in_dir},dst=/work/in,readonly",
            "--mount", f"type=bind,src={out_dir},dst=/work/out"]
    if gate_dir is not None:
        argv += ["--mount", f"type=bind,src={gate_dir},dst=/run/gate,readonly"]
    argv += ["--user", f"{UID}:{UID}", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
             "--pids-limit", "256", "--memory", "3g", "--memory-swap", "3g", "--cpus", "1.5",
             "--ipc", "private", "--shm-size", "256m",
             "--ulimit", "nofile=1024:1024", "--ulimit", "fsize=268435456", "--ulimit", "core=0",
             "--init", "--stop-timeout", "5", "--hostname", "sandbox",
             "--log-opt", "max-size=5m", "--log-opt", "max-file=1",
             "--env", "HOME=/home/agent", "--env", f"PILOT_RUN={run_id}",
             *[x for k, v in sorted((extra_env or {}).items()) if k.startswith("PILOT_") for x in ("--env", f"{k}={v}")],
             "--entrypoint", entry[0], image, *entry[1:]]
    t0 = time.time()
    started = sh(argv, check=False)
    res = {"name": name, "docker_run_rc": started.returncode, "docker_run_err": started.stderr[-500:]}
    if started.returncode != 0:
        return res
    try:
        w = sh(["docker", "wait", name], check=False, timeout=wall_s + 30)
        res["exit_code"] = int(w.stdout.strip() or -1)
    except subprocess.TimeoutExpired:
        res["exit_code"], res["wall_clock_limit_hit"] = None, True
    res["wall_s"] = round(time.time() - t0, 1)
    insp = sh(["docker", "inspect", name], check=False)
    try:
        hc = json.loads(insp.stdout)[0]["HostConfig"]
        res["effective"] = {k: hc.get(k) for k in ("NetworkMode", "ReadonlyRootfs", "CapDrop", "CapAdd", "SecurityOpt",
                                                   "PidsLimit", "Memory", "MemorySwap", "NanoCpus", "Privileged",
                                                   "IpcMode", "Ulimits", "Init")}
        res["oom_killed"] = json.loads(insp.stdout)[0]["State"].get("OOMKilled")
    except Exception:
        pass
    logs = sh(["docker", "logs", "--tail", "200", name], check=False)
    res["log_tail"] = (logs.stdout + logs.stderr)[-6000:]
    sh(["docker", "rm", "-f", name], check=False)
    res["removed"] = sh(["docker", "inspect", name], check=False).returncode != 0
    return res


def release(tdir: Path) -> None:
    """Make out/ readable for extraction (no symlink following), then unmount both tmpfs."""
    o = tdir / "out"
    sh(["sudo", "chown", "-R", "-P", f"{os.getuid()}:{os.getgid()}", str(o)], check=False)
    sh(["sudo", "chmod", "-R", "u+rwX", str(o)], check=False)


def unmount(tdir: Path) -> None:
    for d in ("out", "in"):
        sh(["sudo", "umount", str(tdir / d)], check=False)
