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
     "block type 'document'"),
    ({"messages": [{"role": "user", "content": [{"type": "document", "source": {
        "type": "base64", "media_type": "application/pdf", "data": "QQ=="}}]}]}, "block type 'document'"),
    ({"messages": [{"role": "user", "content": [{"type": "search_result", "source": "x", "title": "t",
                                                 "content": []}]}]}, "block type"),
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


def test_cache_markers_normalized_for_every_arm(gate):
    g, d = gate
    g.open("cc01")
    body = dict(BASE, system=[{"type": "text", "text": "s", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
                tools=[{"name": "t", "input_schema": {"type": "object"}, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "b", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]}],
                cache_control={"type": "ephemeral", "ttl": "1h"})
    st, _ = g.messages("cc01", body)
    assert st == 200
    sent = json.loads((d / "log" / "bodies" / "cc01" / "001.request.json").read_text())
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in json.dumps({k: sent[k] for k in ("system", "tools", "messages")})
    rec = [x for x in ledger(d) if x.get("event") == "request"][0]
    assert rec["cache_markers_stripped"] == 4


def test_usd_prices_one_hour_cache_writes_at_double_input():
    u = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1_000_000,
         "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 1_000_000}}
    assert abs(G.usd(G.AGENT_MODEL, u) - 2.0) < 1e-9
    u["cache_creation"] = {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 0}
    assert abs(G.usd(G.AGENT_MODEL, u) - 1.25) < 1e-9


def test_worst_case_counts_tools_and_multibyte_text():
    ja = "\u3042" * 3000   # 3000 chars, 9000 UTF-8 bytes
    body = {"model": "x", "max_tokens": 10, "tools": [{"name": "t", "description": "d" * 6000,
                                                       "input_schema": {"type": "object"}}],
            "messages": [{"role": "user", "content": ja}]}
    _, f = G.apply_policy(body, "agent", {})
    assert f["worst_input_tokens"] >= 9000 + 6000
    assert f["est_input_tokens"] >= (9000 + 6000) // 3
    big = {"model": "x", "max_tokens": 10, "messages": [{"role": "user", "content": "a" * 900_000}]}
    _, f = G.apply_policy(big, "agent", {})
    assert f["worst_input_tokens"] == G.CONTEXT_TOKENS[G.AGENT_MODEL]


def test_unknown_cost_is_charged_worst_case(gate, monkeypatch):
    g, d = gate
    def boom(run, body):
        raise ConnectionResetError("client went away")
    monkeypatch.setattr(G.fake_model, "respond", boom)
    g.open("wc01")
    st, _ = g.messages("wc01", dict(BASE))
    assert st == 502
    rec = [x for x in ledger(d) if x.get("event") == "request"][0]
    assert rec["charged_worst_case"] is True and rec["usd"] == rec["reserved_worst_usd"] > 0
    s = g.close("wc01")
    assert abs(s["usd"] - rec["reserved_worst_usd"]) < 1e-6
    assert G.STATE.reserved == {"agent": 0.0, "judge": 0.0}


def test_agent_runs_cannot_spend_the_judge_share(gate, monkeypatch):
    g, _ = gate
    G.STATE.spent["agent"] = G.PILOT_USD_CAP - G.JUDGE_USD_CAP - 0.001
    g.open("ag01")
    st, j = g.messages("ag01", dict(BASE))
    assert st == 400 and "agent USD cap" in j["error"]["message"]
    g.open("jd01", kind="judge")
    st, _ = g.messages("jd01", dict(BASE, max_tokens=100))
    assert st == 200


def test_bad_content_length_refused(gate):
    import socket
    g, d = gate
    g.open("cl01")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(f"{d}/runs/cl01/run.sock")
    s.sendall(b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n")
    s.settimeout(5)
    assert b" 400 " in s.recv(4096).split(b"\r\n")[0] + b" "
    s.close()


def test_cache_strip_leaves_schemas_and_tool_inputs_alone(gate):
    g, d = gate
    g.open("cc02")
    schema = {"type": "object", "properties": {"cache_control": {"type": "string"}}, "required": ["cache_control"]}
    msgs = [{"role": "user", "content": "x"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tu1", "name": "t",
                                               "input": {"cache_control": "keep"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": [
                {"type": "text", "text": "r", "cache_control": {"type": "ephemeral"}}]}]}]
    st, _ = g.messages("cc02", dict(BASE, tools=[{"name": "t", "input_schema": schema}], messages=msgs))
    assert st == 200
    sent = json.loads((d / "log" / "bodies" / "cc02" / "001.request.json").read_text())
    assert sent["tools"][0]["input_schema"] == schema
    assert sent["messages"][1]["content"][0]["input"] == {"cache_control": "keep"}
    assert "cache_control" not in sent["messages"][2]["content"][0]["content"][0]
