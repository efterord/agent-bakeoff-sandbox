"""Gate policy and caps, in-process (fake mode, stdlib only)."""
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

from gate import gate as G
from pilot.gatectl import Gate


@pytest.fixture()
def gate(monkeypatch):
    # unix socket paths must be short: use a short dir under the test's TMPDIR
    d = Path(tempfile.mkdtemp(prefix="g", dir=os.environ.get("PILOT_TEST_SOCKDIR", None)))
    for k in ("ctl", "runs", "log"):
        (d / k).mkdir()
    G.STATE = G.State("fake", d / "log", d / "runs", None)
    srv = G.UnixHTTPServer(str(d / "ctl" / "ctl.sock"), G.CtlHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.05)
    yield Gate(str(d / "ctl" / "ctl.sock"), str(d / "runs")), d
    srv.shutdown()
    srv.server_close()


BASE = {"model": "claude-opus-5", "max_tokens": 99999, "temperature": 0.7, "metadata": {"user_id": "x"},
        "messages": [{"role": "user", "content": "hello"}]}


def ledger(d, want=1):
    # the gate writes its ledger line right after sending the response: poll briefly
    for _ in range(100):
        rows = [json.loads(x) for x in (d / "log" / "ledger.jsonl").read_text().splitlines()]
        if sum(1 for r in rows if r.get("event") == "request") >= want:
            return rows
        time.sleep(0.02)
    return rows


def test_model_pinned_and_params_rewritten(gate):
    g, d = gate
    g.open("run1", task="P2-sakura-ja")
    st, j = g.messages("run1", dict(BASE), headers={"anthropic-beta": "x-2026"})
    assert st == 200 and j["model"] == G.AGENT_MODEL
    rec = [x for x in ledger(d) if x.get("event") == "request"][0]
    assert rec["model"] == G.AGENT_MODEL and rec["requested_model"] == "claude-opus-5"
    assert rec["max_tokens"] == 8192 and "temperature" in rec["stripped"] and rec["betas_dropped"] == "x-2026"
    body = json.loads((d / "log" / "bodies" / "run1" / "001.request.json").read_text())
    assert body["cache_control"] == {"type": "ephemeral"} and "temperature" not in body and "metadata" not in body


def test_judge_kind_pins_judge_model_and_disables_thinking(gate):
    g, d = gate
    g.open("judge1", kind="judge")
    st, j = g.messages("judge1", dict(BASE))
    assert st == 200 and j["model"] == G.JUDGE_MODEL
    body = json.loads((d / "log" / "bodies" / "judge1" / "001.request.json").read_text())
    assert body["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("extra,why", [
    ({"tools": [{"type": "web_search_20260209", "name": "web_search"}]}, "server tool"),
    ({"tools": [{"name": "t", "input_schema": {}, "defer_loading": True}]}, "tool fields"),
    ({"mcp_servers": [{"type": "url", "url": "https://example.com", "name": "m"}]}, "mcp_servers"),
    ({"container": {"skills": []}}, "container"),
    ({"betas": ["x"]}, "betas"),
    ({"surprise": 1}, "unknown parameter"),
    ({"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "https://e/x"}}]}]},
     "base64 only"),
    ({"messages": [{"role": "user", "content": [{"type": "document", "source": {"type": "file", "file_id": "f"}}]}]},
     "base64 only"),
    ({"messages": [{"role": "user", "content": "token sk-ant-api03-abcdefghijklmnop"}]}, "tripwire"),
])
def test_policy_rejections(gate, extra, why):
    g, _ = gate
    g.open("run2")
    st, j = g.messages("run2", dict(BASE, **extra))
    assert st == 400 and why in j["error"]["message"]


def test_nested_tool_result_images_are_checked(gate):
    g, _ = gate
    g.open("run3")
    msg = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": [
        {"type": "image", "source": {"type": "url", "url": "https://e/x.png"}}]}]}]
    st, j = g.messages("run3", dict(BASE, messages=msg))
    assert st == 400


def test_caps(gate):
    g, _ = gate
    g.open("cap1", caps={"max_requests": 2})
    codes = [g.messages("cap1", dict(BASE))[0] for _ in range(3)]
    assert codes == [200, 200, 400]
    g.open("cap2", caps={"max_usd": 0.000001})
    st, j = g.messages("cap2", dict(BASE))
    assert st == 400 and j["error"]["type"] == "budget_exhausted"
    g.open("cap3", caps={"max_images": 1})
    imgs = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": c}} for c in ("QQ==", "Qg==")]
    st, j = g.messages("cap3", dict(BASE, messages=[{"role": "user", "content": imgs}]))
    assert st == 400 and "image" in j["error"]["message"]
    s = g.close("cap3")
    assert s["capped"] == 1


def test_caps_only_tighten(gate):
    g, _ = gate
    info = g.open("cap4", caps={"max_usd": 50, "max_requests": 1000, "max_images": 999})
    assert info["caps"]["max_usd"] == 0.5 and info["caps"]["max_requests"] == 80 and info["caps"]["max_images"] == 32


def test_pilot_cap(gate, monkeypatch):
    g, _ = gate
    monkeypatch.setattr(G, "PILOT_USD_CAP", 0.0001)
    g.open("cap5")
    st, j = g.messages("cap5", dict(BASE))
    assert st == 400 and "pilot USD cap" in j["error"]["message"]


def test_endpoints(gate):
    from pilot.gatectl import call
    g, d = gate
    g.open("ep01")
    sock = f"{d}/runs/ep01/run.sock"
    assert call(sock, "GET", "/v1/models")[0] == 200
    assert call(sock, "POST", "/v1/files", {})[0] == 403
    assert call(sock, "POST", "/v1/messages/batches", {})[0] == 403
    assert call(sock, "GET", "/v1/organizations")[0] == 403
    g.close("ep01")
    assert not os.path.exists(sock)


def test_real_mode_refuses_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("GATE_MODE", "real")
    monkeypatch.setenv("GATE_LOG_DIR", str(tmp_path / "l"))
    monkeypatch.delenv("GATE_REAL_ENABLE", raising=False)
    with pytest.raises(SystemExit, match="disabled"):
        G.main()
    monkeypatch.setenv("GATE_REAL_ENABLE", "1")
    monkeypatch.setenv("GATE_KEY_FILE", str(tmp_path / "nokey"))
    with pytest.raises(SystemExit, match="key file"):
        G.main()


def test_model_override_env_refused(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "claude-opus-5")
    with pytest.raises(SystemExit, match="pins"):
        G.main()


def test_fake_stream_shape(gate):
    from pilot.gatectl import UnixHTTP
    g, d = gate
    g.open("st01", task="P2-sakura-ja")
    c = UnixHTTP(f"{d}/runs/st01/run.sock")
    c.request("POST", "/v1/messages", body=json.dumps(dict(BASE, stream=True)).encode(),
              headers={"content-type": "application/json"})
    r = c.getresponse()
    data = r.read().decode()
    assert r.status == 200 and "event: message_start" in data and "event: message_stop" in data
