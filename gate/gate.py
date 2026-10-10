"""Model gate for the PPTX pilot (stdlib only).

The gate is the ONLY component that can reach a model provider, and the only
holder of the provider key. Sandboxed agents reach it through one per-run unix
socket each; the harness controls it through a control socket that is never
mounted into a sandbox.

Modes
  fake  scripted Anthropic-shaped replies (gate/fake_model.py). No network, no key.
  real  forwards to https://api.anthropic.com. Refuses to start unless
        GATE_REAL_ENABLE=1 AND a key file exists at GATE_KEY_FILE. The key is
        read from that file only (never from the environment).

Policy (identical for every arm; enforced per request)
  * endpoints: POST /v1/messages, POST /v1/messages/count_tokens; GET /v1/models
    is answered locally with the pinned id. Everything else -> 403.
  * model is OVERWRITTEN with the pinned id for the run kind (agent / judge).
  * temperature/top_p/top_k/metadata/service_tier/output_config/inference_geo
    are dropped; thinking is forced off; max_tokens is clamped to 8192.
  * caching is identical for every arm: every client cache_control marker
    (system, messages, tools; any TTL) is removed and one top-level automatic
    5-minute cache_control is added (the API allows 4 breakpoints in total, and
    a 1-hour TTL would be billed at 2x input).
  * rejected: unknown top-level keys, mcp_servers, container,
    context_management, fallbacks, speed, any non-custom tool (server tools),
    tool fields other than name/description/input_schema/cache_control/type,
    content blocks other than text/image/tool_use/tool_result (documents are
    refused: they would bypass the image cap), image sources other than
    base64, > 30 images per request, bodies > 16 MB. anthropic-beta (and every
    other client header) is never forwarded; its presence is logged.
  * caps: per run <= 80 requests, <= 32 distinct image hashes, <= USD 0.50,
    <= 15 min since open; per pilot <= USD 10, split agent <= USD 7.5 and
    judge <= USD 2.5 (so agent runs can never starve the judge).
    Two reservations are made under one lock before a request is sent:
      - an ESTIMATE (UTF-8 bytes / 3 + image tokens + max_tokens output) against
        the per-run USD cap, a fairness budget that may be overshot by at most
        the estimation error of the run's last admitted request;
      - a WORST CASE (every UTF-8 byte a token, capped at the context window,
        + the model's per-image maximum, all priced at the cache-write rate,
        + max_tokens output) against the pilot / agent / judge caps, which
        therefore cannot be overshot.
    A request whose cost is unknown (upstream exception, client disconnect,
    stream without a final message_delta, unparsable 200) is charged its worst
    case. A cap returns HTTP 400
    {"type":"error","error":{"type":"budget_exhausted",...}} to every arm alike.
  * the pilot cap lives in this process only: a gate restart or a new workflow
    run starts at zero. The provider-side spend limit on the dedicated key is
    the cross-run backstop.
  * tripwire: key-like strings in text are blocked; synthetic PII shapes are
    logged (the fixtures contain them on purpose).
  * ledger: one JSONL line per request; full request/response bodies saved
    under the log dir.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import socket
import socketserver
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gate import fake_model  # noqa: E402

# ---------------------------------------------------------------- pins ----
AGENT_MODEL = "claude-haiku-4-5-20251001"
JUDGE_MODEL = "claude-sonnet-5"
PINNED = {"agent": AGENT_MODEL, "judge": JUDGE_MODEL}
# USD per MTok: input, output, cache write (5 min), cache read. Frozen 2026-10-11.
PRICES = {
    AGENT_MODEL: (1.00, 5.00, 1.25, 0.10),
    JUDGE_MODEL: (2.00, 10.00, 2.50, 0.20),
}
UPSTREAM_HOST = "api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# Context window and per-image token ceiling per pinned model (worst-case reservation).
CONTEXT_TOKENS = {AGENT_MODEL: 200_000, JUDGE_MODEL: 1_000_000}
IMAGE_TOKENS_MAX = {AGENT_MODEL: 1_600, JUDGE_MODEL: 4_800}
OVERHEAD_TOKENS = 2_000   # tool-use system prompt and message framing added by the API

DEFAULT_RUN_CAPS = {"max_requests": 80, "max_images": 32, "max_usd": 0.50, "max_wall_s": 900}
PILOT_USD_CAP = 10.0
JUDGE_USD_CAP = 2.5
MAX_TOKENS = 8192
MAX_BODY = 16 * 1024 * 1024
MAX_IMAGES_PER_REQUEST = 30

ALLOWED_KEYS = {"model", "messages", "system", "max_tokens", "tools", "tool_choice", "stream", "stop_sequences",
                "cache_control"}
STRIP_KEYS = {"temperature", "top_p", "top_k", "metadata", "service_tier", "output_config", "inference_geo",
              "thinking"}
REJECT_KEYS = {"mcp_servers", "container", "context_management", "fallbacks", "speed", "betas", "diagnostics"}
TOOL_KEYS = {"name", "description", "input_schema", "cache_control", "type"}
BLOCK_TYPES = {"text", "image", "tool_use", "tool_result"}

KEY_LIKE = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                      r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
PII_SHAPE = re.compile(r"\b0[39]0?-0000-0000\b")


class PolicyError(Exception):
    def __init__(self, status, etype, msg):
        super().__init__(msg)
        self.status, self.etype, self.msg = status, etype, msg


def err_body(etype, msg):
    return {"type": "error", "error": {"type": etype, "message": msg}}


# ------------------------------------------------------------- state ----
class State:
    def __init__(self, mode, log_dir: Path, runs_dir: Path, key: str | None):
        self.mode, self.log_dir, self.runs_dir, self.key = mode, log_dir, runs_dir, key
        self.lock = threading.Lock()
        self.runs = {}            # run_id -> dict
        self.servers = {}         # run_id -> server
        self.spent = {"agent": 0.0, "judge": 0.0}
        self.reserved = {"agent": 0.0, "judge": 0.0}   # worst-case reservations in flight
        self.ledger = open(log_dir / "ledger.jsonl", "a", encoding="utf-8")

    def total_spent(self):
        return self.spent["agent"] + self.spent["judge"]

    def kind_cap(self, kind):
        return JUDGE_USD_CAP if kind == "judge" else PILOT_USD_CAP - JUDGE_USD_CAP


STATE: State | None = None


def usd(model, usage):
    pin, pout, pcw, pcr = PRICES[model]
    cw = int(usage.get("cache_creation_input_tokens") or 0)
    br = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    cw1h = int(br.get("ephemeral_1h_input_tokens") or 0)   # never expected (markers are stripped); 2x input
    cw_cost = max(0, cw - cw1h) * pcw + cw1h * 2 * pin
    return (int(usage.get("input_tokens") or 0) * pin + int(usage.get("output_tokens") or 0) * pout
            + cw_cost + int(usage.get("cache_read_input_tokens") or 0) * pcr) / 1e6


def _strip_cache_markers(body: dict) -> int:
    """Remove cache_control from every place the API reads it: system blocks, tool definitions, message
    content blocks and blocks nested in tool_result content. Tool schemas and tool_use inputs are data
    and are left untouched. Returns the number of markers removed."""
    def pop(d):
        return 1 if isinstance(d, dict) and d.pop("cache_control", None) is not None else 0
    n = 0
    if isinstance(body.get("system"), list):
        n += sum(pop(b) for b in body["system"])
    for t in body.get("tools") or []:
        n += pop(t)
    for m in body.get("messages") or []:
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            n += sum(pop(b) for b in _walk_blocks(m["content"]))
    return n


def _walk_blocks(content):
    """Yield every content block, including blocks nested in tool_result content."""
    if isinstance(content, str) or content is None:
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        yield b
        if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
            yield from _walk_blocks(b["content"])


def apply_policy(body: dict, kind: str, headers) -> tuple[dict, dict]:
    """Return (rewritten body, facts). Raises PolicyError."""
    facts = {"stripped": [], "betas_dropped": headers.get("anthropic-beta") or "", "pii_shapes": 0}
    for k in list(body):
        if k in REJECT_KEYS:
            raise PolicyError(400, "invalid_request_error", f"gate: parameter '{k}' is not allowed")
        if k in STRIP_KEYS:
            body.pop(k)
            facts["stripped"].append(k)
        elif k not in ALLOWED_KEYS:
            raise PolicyError(400, "invalid_request_error", f"gate: unknown parameter '{k}' refused")
    facts["requested_model"] = body.get("model")
    body["model"] = PINNED[kind]
    if kind == "judge":
        body["thinking"] = {"type": "disabled"}   # Sonnet 5 runs adaptive when omitted
    try:
        mt = int(body.get("max_tokens") or 4096)
    except (TypeError, ValueError):
        mt = 4096
    body["max_tokens"] = max(1, min(mt, MAX_TOKENS))
    body.pop("cache_control", None)
    facts["cache_markers_stripped"] = _strip_cache_markers(body)
    body["cache_control"] = {"type": "ephemeral"}
    for t in body.get("tools") or []:
        if not isinstance(t, dict):
            raise PolicyError(400, "invalid_request_error", "gate: malformed tool")
        if t.get("type") not in (None, "custom"):
            raise PolicyError(400, "invalid_request_error", f"gate: server tool '{t.get('type')}' refused")
        extra = set(t) - TOOL_KEYS
        if extra or "input_schema" not in t or "name" not in t:
            raise PolicyError(400, "invalid_request_error", f"gate: tool fields refused: {sorted(extra)}")
    images, texts, image_chars = [], [], 0
    sysp = body.get("system")
    if isinstance(sysp, str):
        texts.append(sysp)
    elif isinstance(sysp, list):
        texts += [b.get("text", "") for b in sysp if isinstance(b, dict)]
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            raise PolicyError(400, "invalid_request_error", "gate: malformed message")
        c = m.get("content")
        if isinstance(c, str):
            texts.append(c)
            continue
        if not isinstance(c, list):
            raise PolicyError(400, "invalid_request_error", "gate: malformed message content")
        for b in _walk_blocks(c):
            t = b.get("type")
            if t not in BLOCK_TYPES:
                raise PolicyError(400, "invalid_request_error", f"gate: block type '{t}' refused")
            if t == "image":
                src = b.get("source") or {}
                if src.get("type") != "base64":
                    raise PolicyError(400, "invalid_request_error",
                                      f"gate: {t} source '{src.get('type')}' refused (base64 only)")
                data = str(src.get("data", ""))
                image_chars += len(data)
                images.append(hashlib.sha256(data.encode()).hexdigest())
            elif t == "text":
                texts.append(b.get("text", ""))
            elif t == "tool_result" and isinstance(b.get("content"), str):
                texts.append(b["content"])
            elif t == "tool_use":
                texts.append(json.dumps(b.get("input", {}), ensure_ascii=False))
    if len(images) > MAX_IMAGES_PER_REQUEST:
        raise PolicyError(400, "invalid_request_error", f"gate: {len(images)} images > {MAX_IMAGES_PER_REQUEST}")
    blob = "\n".join(texts)
    if KEY_LIKE.search(blob):
        raise PolicyError(400, "invalid_request_error", "gate: key-like string in request blocked (tripwire)")
    facts["pii_shapes"] = len(PII_SHAPE.findall(blob))
    facts["image_hashes"] = images
    # Everything that is not image data counts as text: system, messages, tool schemas, JSON framing.
    # base64 is ASCII, so its UTF-8 length equals its character count.
    text_bytes = max(0, len(json.dumps(body, ensure_ascii=False).encode()) - image_chars)
    model = body["model"]
    img_tokens = IMAGE_TOKENS_MAX[model] * len(images)
    facts["est_input_tokens"] = text_bytes // 3 + img_tokens + OVERHEAD_TOKENS
    facts["worst_input_tokens"] = min(CONTEXT_TOKENS[model], text_bytes + img_tokens + OVERHEAD_TOKENS)
    facts["tools_offered"] = [t.get("name") for t in body.get("tools") or []]
    return body, facts


# ----------------------------------------------------------- handler ----
class RunHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pilot-gate"

    def log_message(self, fmt, *args):
        pass

    def address_string(self):
        return "unix"

    @property
    def run(self):
        return self.server.run  # type: ignore[attr-defined]

    def _send(self, status, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return data

    def do_GET(self):
        if self.path.split("?")[0].rstrip("/") == "/v1/models":
            m = PINNED[self.run["kind"]]
            return self._send(200, {"data": [{"type": "model", "id": m, "display_name": m,
                                              "created_at": "2025-10-01T00:00:00Z"}],
                                    "has_more": False, "first_id": m, "last_id": m})
        self._ledger({"event": "refused", "path": self.path, "method": "GET"})
        return self._send(403, err_body("permission_error", "gate: endpoint not allowed"))

    def do_PUT(self):
        return self._forbid()

    def do_DELETE(self):
        return self._forbid()

    def do_PATCH(self):
        return self._forbid()

    def _forbid(self):
        self._ledger({"event": "refused", "path": self.path, "method": self.command})
        return self._send(403, err_body("permission_error", "gate: endpoint not allowed"))

    def _ledger(self, rec):
        st = STATE
        rec = {"ts": round(time.time(), 3), "run": self.run["id"], **rec}
        with st.lock:
            st.ledger.write(json.dumps(rec, ensure_ascii=False) + "\n")
            st.ledger.flush()

    def do_POST(self):
        st, run = STATE, self.run
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/v1/messages", "/v1/messages/count_tokens"):
            return self._forbid()
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            self.close_connection = True
            return self._send(400, err_body("invalid_request_error", "gate: bad Content-Length"))
        if n > MAX_BODY:
            self._ledger({"event": "refused", "reason": "body too large", "bytes": n})
            self.close_connection = True
            return self._send(413, err_body("request_too_large", "gate: body > 16 MB"))
        raw = self.rfile.read(n) if n else b"{}"
        t0 = time.time()
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send(400, err_body("invalid_request_error", "gate: body is not a JSON object"))
        try:
            body, facts = apply_policy(body, run["kind"], self.headers)
        except PolicyError as e:
            with st.lock:
                run["refused"] += 1
            self._ledger({"event": "refused", "reason": e.msg})
            return self._send(e.status, err_body(e.etype, e.msg))
        model = body["model"]
        if path.endswith("count_tokens"):
            body.pop("max_tokens", None)
            body.pop("cache_control", None)
            body.pop("stream", None)
            if st.mode == "fake":
                return self._send(200, {"input_tokens": facts["est_input_tokens"]})
            status, data, _ = upstream_plain(st.key, "/v1/messages/count_tokens", body)
            self._ledger({"event": "count_tokens", "status": status})
            return self._send(status, data)
        # ---- caps: estimate for the per-run budget, worst case for the money caps
        pin, pout, pcw, _ = PRICES[model]
        kind = run["kind"]
        reserve = (facts["est_input_tokens"] * pcw + body["max_tokens"] * pout) / 1e6
        worst = (facts["worst_input_tokens"] * pcw + body["max_tokens"] * pout) / 1e6
        with st.lock:
            caps = run["caps"]
            new_imgs = set(facts["image_hashes"]) - run["images"]
            reason = None
            if run["closed"]:
                reason = "run closed"
            elif time.time() - run["opened"] > caps["max_wall_s"]:
                reason = "run wall-clock cap"
            elif run["requests"] >= caps["max_requests"]:
                reason = f"run request cap ({caps['max_requests']})"
            elif len(run["images"]) + len(new_imgs) > caps["max_images"]:
                reason = f"run distinct-image cap ({caps['max_images']})"
            elif run["usd"] + run["reserved"] + reserve > caps["max_usd"]:
                reason = f"run USD cap ({caps['max_usd']})"
            elif st.total_spent() + sum(st.reserved.values()) + worst > PILOT_USD_CAP:
                reason = f"pilot USD cap ({PILOT_USD_CAP})"
            elif st.spent[kind] + st.reserved[kind] + worst > st.kind_cap(kind):
                reason = f"{kind} USD cap ({st.kind_cap(kind)})"
            if reason:
                run["capped"] += 1
            else:
                run["requests"] += 1
                run["images"] |= new_imgs
                run["reserved"] += reserve
                st.reserved[kind] += worst
                seq = run["requests"]
        if reason:
            self._ledger({"event": "budget_exhausted", "reason": reason})
            return self._send(400, err_body("budget_exhausted", "gate: " + reason))
        stream = bool(body.get("stream"))
        save_dir = st.log_dir / "bodies" / run["id"]
        try:   # full logging is a precondition: never send what could not be logged
            save_dir.mkdir(parents=True, exist_ok=True)
            (save_dir / f"{seq:03d}.request.json").write_bytes(json.dumps(body, ensure_ascii=False).encode())
        except OSError:
            with st.lock:
                run["reserved"] -= reserve
                st.reserved[kind] -= worst
            return self._send(500, err_body("api_error", "gate: request log write failed; not sent"))
        usage, status, stop, used_tools, err = {}, 0, None, [], None
        self.upstream_request_id = None
        try:
            if st.mode == "fake":
                msg = fake_model.respond(run, body)
                status = 200
                usage, stop = msg["usage"], msg["stop_reason"]
                used_tools = [b["name"] for b in msg["content"] if b["type"] == "tool_use"]
                if stream:
                    data = fake_model.to_sse(msg).encode()
                    self._send(200, data, "text/event-stream")
                else:
                    data = self._send(200, msg)
            else:
                status, data, usage, stop, used_tools = self._forward_real(body, stream)
        except Exception as e:  # never leave a reservation behind
            err = f"{type(e).__name__}: {e}"[:300]
            data = b""
            try:
                self._send(502, err_body("api_error", "gate: upstream error"))
            except Exception:
                pass
        try:
            cost = usd(model, usage) if usage else 0.0
        except (TypeError, ValueError, AttributeError):
            cost, err = worst, err or "unparsable usage"
        # Unknown cost (exception, client gone, truncated stream, unparsable 200): charge the worst case.
        complete = status == 200 and bool(usage) and (stop is not None or not stream)
        charged_worst = err is not None or (status == 200 and not complete)
        if charged_worst:
            cost = max(cost, worst)
        with st.lock:
            run["reserved"] -= reserve
            st.reserved[kind] -= worst
            run["usd"] += cost
            st.spent[kind] += cost
            for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                run["usage"][k] = run["usage"].get(k, 0) + int(usage.get(k) or 0)
        (save_dir / f"{seq:03d}.response.{'sse' if stream else 'json'}").write_bytes(
            data if isinstance(data, bytes) else json.dumps(data).encode())
        self._ledger({"event": "request", "seq": seq, "kind": run["kind"], "arm": run["arm"], "task": run["task"],
                      "model": model, "requested_model": facts["requested_model"], "status": status,
                      "stream": stream, "latency_s": round(time.time() - t0, 3), "usage": usage,
                      "usd": round(cost, 6), "images": len(facts["image_hashes"]),
                      "new_images": len(set(facts["image_hashes"])), "max_tokens": body["max_tokens"],
                      "charged_worst_case": charged_worst, "reserved_est_usd": round(reserve, 6),
                      "reserved_worst_usd": round(worst, 6), "upstream_request_id": self.upstream_request_id,
                      "cache_markers_stripped": facts["cache_markers_stripped"],
                      "stripped": facts["stripped"], "betas_dropped": facts["betas_dropped"],
                      "pii_shapes": facts["pii_shapes"], "tools_offered": facts["tools_offered"],
                      "tools_used": used_tools, "stop_reason": stop, "error": err,
                      "request_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()})

    def _forward_real(self, body, stream):
        conn = http.client.HTTPSConnection(UPSTREAM_HOST, 443, timeout=300, context=ssl.create_default_context())
        try:
            return self._forward_on(conn, body, stream)
        finally:
            conn.close()   # also on a client disconnect mid-stream: stop reading upstream at once

    def _forward_on(self, conn, body, stream):
        payload = json.dumps(body).encode()
        conn.request("POST", "/v1/messages", body=payload, headers={
            "content-type": "application/json", "x-api-key": STATE.key, "anthropic-version": ANTHROPIC_VERSION})
        resp = conn.getresponse()
        self.upstream_request_id = resp.getheader("request-id")
        usage, stop, tools = {}, None, []
        if not stream or resp.status != 200:
            data = resp.read()
            try:
                j = json.loads(data)
                usage = j.get("usage") or {}
                stop = j.get("stop_reason")
                tools = [b.get("name") for b in j.get("content") or [] if b.get("type") == "tool_use"]
            except ValueError:
                pass
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            conn.close()
            return resp.status, data, usage, stop, tools
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        captured = []
        for line in resp:
            self.wfile.write(line)
            self.wfile.flush()
            captured.append(line)
            if line.startswith(b"data: "):
                try:
                    ev = json.loads(line[6:])
                except ValueError:
                    continue
                if ev.get("type") == "message_start":
                    usage.update((ev.get("message") or {}).get("usage") or {})
                elif ev.get("type") == "message_delta":
                    usage.update({k: v for k, v in (ev.get("usage") or {}).items() if v is not None})
                    stop = (ev.get("delta") or {}).get("stop_reason") or stop
                elif ev.get("type") == "content_block_start":
                    cb = ev.get("content_block") or {}
                    if cb.get("type") == "tool_use":
                        tools.append(cb.get("name"))
        conn.close()
        return 200, b"".join(captured), usage, stop, tools


def upstream_plain(key, path, body):
    conn = http.client.HTTPSConnection(UPSTREAM_HOST, 443, timeout=60, context=ssl.create_default_context())
    conn.request("POST", path, body=json.dumps(body).encode(), headers={
        "content-type": "application/json", "x-api-key": key, "anthropic-version": ANTHROPIC_VERSION})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data, {}


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


# ----------------------------------------------------------- control ----
class CtlHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def address_string(self):
        return "ctl"

    def _send(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        st = STATE
        if self.path == "/health":
            return self._send(200, {"ok": True, "mode": st.mode, "agent_model": AGENT_MODEL,
                                    "judge_model": JUDGE_MODEL, "pilot_usd_cap": PILOT_USD_CAP})
        if self.path == "/summary":
            with st.lock:
                return self._send(200, {"mode": st.mode, "spent": st.spent, "runs": {
                    k: summarize(v) for k, v in st.runs.items()}})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        st = STATE
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/open":
            rid = str(req["run_id"])
            if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", rid):
                return self._send(400, {"error": "bad run id"})
            kind = req.get("kind", "agent")
            if kind not in PINNED:
                return self._send(400, {"error": "bad kind"})
            caps = dict(DEFAULT_RUN_CAPS)
            for k, v in (req.get("caps") or {}).items():
                if k in caps:   # caps may only be tightened, never loosened
                    caps[k] = min(caps[k], type(caps[k])(v))
            d = st.runs_dir / rid
            d.mkdir(parents=True, exist_ok=False)
            os.chmod(d, 0o755)
            sock = d / "run.sock"
            run = {"id": rid, "kind": kind, "arm": req.get("arm", ""), "task": req.get("task", ""),
                   "scenario": req.get("scenario", ""), "caps": caps, "opened": time.time(), "requests": 0,
                   "refused": 0, "capped": 0, "images": set(), "usd": 0.0, "reserved": 0.0, "usage": {},
                   "closed": False}
            srv = UnixHTTPServer(str(sock), RunHandler)
            srv.run = run  # type: ignore[attr-defined]
            os.chmod(sock, 0o666)  # the sandbox uid must be able to connect(); the dir holds nothing else
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            with st.lock:
                st.runs[rid], st.servers[rid] = run, srv
            return self._send(200, {"ok": True, "socket_dir": str(d), "caps": caps, "model": PINNED[kind]})
        if self.path == "/close":
            rid = str(req["run_id"])
            with st.lock:
                run, srv = st.runs.get(rid), st.servers.pop(rid, None)
                if run:
                    run["closed"] = True
            if srv:
                srv.shutdown()
                srv.server_close()
                try:
                    os.unlink(st.runs_dir / rid / "run.sock")
                except OSError:
                    pass
            return self._send(200, summarize(run) if run else {"error": "unknown run"})
        return self._send(404, {"error": "not found"})


def summarize(run):
    return {k: (sorted(v) if isinstance(v, set) else v) for k, v in run.items() if k != "images"} | {
        "distinct_images": len(run["images"])}


def main():
    global STATE
    mode = os.environ.get("GATE_MODE", "fake")
    log_dir = Path(os.environ.get("GATE_LOG_DIR", "/var/log/gate"))
    runs_dir = Path(os.environ.get("GATE_RUNS_DIR", "/run/gate-runs"))
    ctl = Path(os.environ.get("GATE_CTL", "/run/gate-ctl/ctl.sock"))
    for k in ("AGENT_MODEL", "JUDGE_MODEL", "MODEL_NAME"):
        if os.environ.get(k):
            sys.exit(f"{k} is set: the gate pins its models in code and refuses overrides")
    key = None
    if mode == "real":
        if os.environ.get("GATE_REAL_ENABLE") != "1":
            sys.exit("real mode is disabled (GATE_REAL_ENABLE != 1)")
        kf = Path(os.environ.get("GATE_KEY_FILE", "/run/secrets/anthropic_key"))
        try:
            key = kf.read_text().strip()
        except OSError:
            sys.exit("real mode needs the key file; none present - refusing to start")
        if not key.startswith("sk-ant-"):
            sys.exit("key file does not look like an Anthropic API key - refusing to start")
    elif mode != "fake":
        sys.exit("GATE_MODE must be fake or real")
    log_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    STATE = State(mode, log_dir, runs_dir, key)
    if ctl.exists():
        ctl.unlink()
    srv = UnixHTTPServer(str(ctl), CtlHandler)
    os.chmod(ctl, 0o600)
    print(f"pilot gate mode={mode} agent={AGENT_MODEL} judge={JUDGE_MODEL} cap=${PILOT_USD_CAP}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
