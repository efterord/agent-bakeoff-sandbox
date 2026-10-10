"""Arm H: Hermes H-exec entrypoint inside the sandbox container.

1. start the forwarder (127.0.0.1:8088 -> /run/gate/run.sock);
2. write the locked config to a tmpfs HERMES_HOME and start `hermes gateway run`
   (API server on 127.0.0.1 only, random per-run key);
3. record Hermes's OWN readiness checks (toolsets, skills list, skill/terminal/
   vision requirement checks) and the skill prerequisites as the terminal sees
   them; FAIL CLOSED (no task run) if the powerpoint skill or its tools are not
   available;
4. post the task to the Runs API, stream events to /work/out/trace/.

Approvals: the harness NEVER approves. PILOT_HERMES_APPROVAL selects what
happens when Hermes's own command guard asks for approval:
  deny (default) - answer "deny", record the request, let the run continue;
  stop           - stop the run at the first request;
  off            - Hermes's guard is switched off inside the sealed sandbox
                   (approvals.mode: off), so nothing is ever asked.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import forwarder  # noqa: E402

IN, OUTD = Path("/work/in"), Path("/work/out")
TRACE = OUTD / "trace"
HERMES = "/opt/hermes/.venv/bin/hermes"
HPY = "/opt/hermes/.venv/bin/python"
PORT = 8642
RUN_LIMIT_S = int(os.environ.get("PILOT_RUN_LIMIT_S", "840"))
APPROVAL = os.environ.get("PILOT_HERMES_APPROVAL", "deny")
MAX_DENIALS = 25
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
REQUIRED_TOOLSETS = {"terminal", "skills", "vision", "file"}
FORBIDDEN_TOOLSETS = {"browser", "code_execution", "web", "delegation", "computer_use", "memory", "cronjob"}

READINESS_PY = r'''
import json, sys
sys.path.insert(0, "/opt/hermes")
out = {}
def tryit(name, fn):
    try:
        out[name] = fn()
    except Exception as e:
        out[name] = "ERROR %s: %s" % (type(e).__name__, str(e)[:300])
def skills():
    from tools import skills_tool as S
    raw = S.skills_list()
    return raw if isinstance(raw, str) else json.dumps(raw, default=str)
def view():
    from tools import skills_tool as S
    raw = S.skill_view("powerpoint")
    return (raw if isinstance(raw, str) else json.dumps(raw, default=str))[:4000]
tryit("check_skills_requirements", lambda: __import__("tools.skills_tool", fromlist=["x"]).check_skills_requirements())
tryit("check_terminal_requirements", lambda: __import__("tools.terminal_tool", fromlist=["x"]).check_terminal_requirements())
tryit("check_vision_requirements", lambda: __import__("tools.vision_tools", fromlist=["x"]).check_vision_requirements())
tryit("skills_list", skills)
tryit("skill_view_powerpoint", view)
print(json.dumps(out, default=str))
'''

PREREQ_SH = ('command -v python3; python3 -c "import pptx, lxml, PIL; print(\'python-pptx\', pptx.__version__)"; '
             'command -v soffice; command -v pdftoppm; ls /opt/hermes/skills/productivity/powerpoint/scripts')


def api(method, path, key, body=None, timeout=30):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                               data=json.dumps(body).encode() if body is not None else None,
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def sh(cmd, login=False, env=None):
    argv = ["bash", "-lc", cmd] if login else ["sh", "-c", cmd]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=env)
        return {"rc": r.returncode, "out": r.stdout[-3000:], "err": r.stderr[-1500:]}
    except Exception as e:
        return {"rc": -1, "err": repr(e)}


def events(run_id, key, stop, sink):
    try:
        r = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/runs/{run_id}/events",
                                   headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(r, timeout=RUN_LIMIT_S + 60) as resp, \
                open(TRACE / "events.jsonl", "a", encoding="utf-8") as fh:
            for raw in resp:
                if stop.is_set():
                    return
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                keep = {k: ev.get(k) for k in ("event", "tool", "error", "duration", "status") if k in ev}
                for k in ("args", "arguments", "input", "preview"):
                    if k in ev:
                        keep[k] = json.dumps(ev[k], ensure_ascii=False)[:2000]
                keep["t"] = round(time.time(), 3)
                sink.append(keep)
                fh.write(json.dumps(keep, ensure_ascii=False) + "\n")
                fh.flush()
                if str(ev.get("event", "")).startswith("run."):
                    return
    except Exception as e:
        sink.append({"event": "stream_error", "error": type(e).__name__})


def main():
    TRACE.mkdir(parents=True, exist_ok=True)
    (OUTD / "w").mkdir(parents=True, exist_ok=True)
    task = json.loads((IN / "task.json").read_text(encoding="utf-8"))
    summary = {"arm": "hermes-hexec", "status": "starting"}
    forwarder.start()
    home = Path(os.environ.get("HOME", "/home/agent"))
    hh = home / ".hermes"
    hh.mkdir(parents=True, exist_ok=True)
    tmpl = Path(__file__).with_name("hermes_config.yaml.tmpl").read_text(encoding="utf-8")
    if APPROVAL == "off":
        tmpl += "approvals:\n  mode: \"off\"\n"
    (hh / "config.yaml").write_text(tmpl, encoding="utf-8")
    summary["approval_policy"] = APPROVAL
    key = secrets.token_urlsafe(32)
    env = dict(os.environ, HERMES_HOME=str(hh), API_SERVER_ENABLED="true", API_SERVER_HOST="127.0.0.1",
               API_SERVER_PORT=str(PORT), API_SERVER_KEY=key, ANTHROPIC_API_KEY="sandbox-no-key",
               ANTHROPIC_BASE_URL="http://127.0.0.1:8088", HERMES_DOCKER_EXEC_AS_ROOT="0")
    glog = open(OUTD / "trace" / "gateway.log.jsonl", "w", encoding="utf-8")
    proc = subprocess.Popen([HERMES, "gateway", "run"], env=env, cwd=str(OUTD / "w"), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace")

    def pump():
        for line in proc.stdout:
            glog.write(json.dumps({"t": round(time.time(), 3), "line": line.rstrip()[:1000]}) + "\n")
            glog.flush()
    threading.Thread(target=pump, daemon=True).start()
    t0 = time.time()
    try:
        ready = None
        while time.time() - t0 < 240:
            try:
                ready = api("GET", "/v1/capabilities", key)
                break
            except Exception:
                if proc.poll() is not None:
                    break
                time.sleep(2)
        if ready is None:
            summary["status"] = "fail_closed:api_server_not_ready"
            return 4
        ts = {}
        try:
            ts = api("GET", "/v1/toolsets", key)
        except Exception as e:
            ts = {"error": repr(e)}
        enabled = sorted(t.get("name") for t in ts.get("data", []) if t.get("enabled"))
        tools = sorted({x for t in ts.get("data", []) if t.get("enabled") for x in (t.get("tools") or [])})
        try:
            r = subprocess.run([HPY, "-c", READINESS_PY], cwd="/opt/hermes", env=env, capture_output=True,
                               text=True, timeout=120)
            own = {"rc": r.returncode, "out": r.stdout[-20000:], "err": r.stderr[-2000:]}
        except Exception as e:
            own = {"rc": -1, "err": repr(e)}
        try:
            own_j = json.loads(own.get("out", "").strip().splitlines()[-1])
        except Exception:
            own_j = {"raw": own}
        pre_login, pre_plain = sh(PREREQ_SH, login=True, env=env), sh(PREREQ_SH, env=env)
        skill_listed = "powerpoint" in str(own_j.get("skills_list", ""))
        skill_viewable = "pptx_create.py" in str(own_j.get("skill_view_powerpoint", ""))
        prereq_ok = all(p.get("rc") == 0 and "python-pptx" in p.get("out", "") and "soffice" in p.get("out", "")
                        and "pdftoppm" in p.get("out", "") for p in (pre_login, pre_plain))
        missing = sorted(REQUIRED_TOOLSETS - set(enabled))
        bad = sorted(FORBIDDEN_TOOLSETS & set(enabled))
        readiness = {"enabled_toolsets": enabled, "enabled_tools": tools, "missing_required": missing,
                     "forbidden_enabled": bad, "hermes_own_checks": own_j, "skill_listed": skill_listed,
                     "skill_viewable": skill_viewable, "prereq_login_shell": pre_login,
                     "prereq_plain_shell": pre_plain, "prereq_ok": prereq_ok,
                     "capabilities_keys": sorted(ready)[:40]}
        ok = not missing and not bad and skill_listed and skill_viewable and prereq_ok
        readiness["ok"] = ok
        (TRACE / "readiness.json").write_text(json.dumps(readiness, indent=1, default=str), encoding="utf-8")
        summary["readiness_ok"] = ok
        if not ok:
            summary["status"] = "fail_closed:doc_skill_unavailable"
            return 4
        created = api("POST", "/v1/runs", key, {"input": task["prompt"], "session_id": "pilot-" + secrets.token_hex(4)})
        run_id = created["run_id"]
        sink, stop = [], threading.Event()
        th = threading.Thread(target=events, args=(run_id, key, stop, sink), daemon=True)
        th.start()
        st, t1, approvals, seen = {}, time.time(), [], set()
        while True:
            try:
                st = api("GET", f"/v1/runs/{run_id}", key)
            except Exception as e:
                st = {"status": "poll_error", "error": repr(e)}
            s = st.get("status")
            if s in TERMINAL:
                break
            if s == "waiting_for_approval":
                appr = st.get("approval") or {}
                rq = appr.get("request_id") or str(appr.get("command", ""))[:200]
                if rq in seen:
                    time.sleep(1)
                    continue
                seen.add(rq)
                rec = {"t": round(time.time(), 3), "pattern": appr.get("pattern_key"),
                       "command": str(appr.get("command", ""))[:1500], "policy": APPROVAL}
                approvals.append(rec)
                with open(TRACE / "approvals.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if APPROVAL != "deny" or len(approvals) > MAX_DENIALS:
                    try:
                        api("POST", f"/v1/runs/{run_id}/stop", key, {})
                    except Exception:
                        pass
                    time.sleep(3)
                    s = "stopped_on_approval"
                    break
                try:  # never approve: deny exactly this request and let the agent continue
                    api("POST", f"/v1/runs/{run_id}/approval", key,
                        {"choice": "deny", "request_id": appr.get("request_id")} if appr.get("request_id")
                        else {"choice": "deny"})
                except urllib.error.HTTPError as e:
                    rec["deny_error"] = e.code
                time.sleep(1)
                continue
            if time.time() - t1 > RUN_LIMIT_S:
                try:
                    api("POST", f"/v1/runs/{run_id}/stop", key, {})
                except Exception:
                    pass
                s = "timeout"
                break
            time.sleep(2)
        stop.set()
        th.join(timeout=5)
        tool_calls = [e.get("tool") for e in sink if e.get("event") == "tool.started"]
        summary.update(status=s or st.get("status"), hermes_status=st.get("status"), tool_calls=len(tool_calls),
                       tools=tool_calls, runtime_usage=st.get("usage") or {}, approval_requests=len(approvals),
                       approval_patterns=sorted({a["pattern"] or "" for a in approvals}),
                       error=str(st.get("error", ""))[:300] or None)
        return 0
    finally:
        summary["wall_s"] = round(time.time() - t0, 1)
        summary["delivered"] = (OUTD / "deliverable.pptx").exists()
        (OUTD / "driver.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
        print(json.dumps(summary, default=str), flush=True)
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        glog.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
