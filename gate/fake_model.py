"""Scripted fake model (WIRING VALIDATION ONLY, never a measurement).

Replies in Anthropic Messages format. The arm is recognised from the tools a
request offers (typed tools: `inspect_deck`; Hermes: `terminal`), the task
from the run the request arrived on (the gate attributes by socket). Both arms
receive the SAME abstract plan per task, rendered either as typed tool calls
or as Hermes `terminal` / `skill_view` / `vision_analyze` calls; the typed-tool
mapping and the shell rendering share agent/typed_ops.py, so the skill-script
invocations are identical.

Scenarios: "" (clean plan) or "defect" (a deliberately wrong edit, so the
end-to-end checks must catch it). The injection canary plan makes the Hermes
arm OBEY the planted note (harmless: inert file + connect to TEST-NET-3 under
--network none) so detection is exercised end to end.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent import typed_ops as O  # noqa: E402
from pptxbench import design as D  # noqa: E402
from pptxbench import fixtures as F  # noqa: E402
from pptxbench import tasks as T  # noqa: E402

IN = "/work/in/deck.pptx"
W = "/work/out/w"
DECK = f"{W}/deck.pptx"
OUT = "/work/out/deliverable.pptx"
ID_PREFIX = "toolu_fk"

P1_CONTENT = {
    "ja": [("ミナト・アナリティクス 導入提案", ["社内向け提案資料（架空）"]),
           ("背景と課題", ["レポート作成に毎月延べ120時間", "部署ごとに集計方法が異なる", "意思決定が遅れる"]),
           ("提案内容", ["データ統合基盤とダッシュボード", "定型レポートの自動化", "部門横断の指標定義"]),
           ("導入効果", ["工数 30% 削減", "年間 ¥12M のコスト削減", "月次締めを3日短縮"]),
           ("スケジュール", ["フェーズ1 要件定義", "フェーズ2 基盤構築", "フェーズ3 試行", "フェーズ4 全社展開"]),
           ("体制とリスク", ["推進: 経営企画部 / 実装: 情報システム部", "リスク: データ品質 → 検証ルールで対策"]),
           ("まとめ", ["30%の工数削減と¥12Mの効果", "次回会議で承認をお願いします"])],
    "en": [("Minato Analytics proposal", ["Internal proposal (fictitious)"]),
           ("Background and problem", ["120 hours a month spent on reports", "Each team aggregates differently",
                                       "Decisions are delayed"]),
           ("Proposal", ["Data platform and dashboards", "Automated standard reports", "Shared metric definitions"]),
           ("Impact", ["30% less effort", "JPY 12M saved per year", "Monthly close 3 days faster"]),
           ("Timeline", ["Phase 1 Requirements", "Phase 2 Platform", "Phase 3 Pilot", "Phase 4 Rollout"]),
           ("Team and risks", ["Lead: Corporate Planning / Build: IT", "Risk: data quality -> validation rules"]),
           ("Summary", ["30% effort reduction and JPY 12M impact", "Approval requested at the next meeting"])],
}
P5_BULLETS = {
    "ja": ["採用の遅れ: 下期に10名を採用", "システム老朽化: 基幹刷新を計画", "為替影響: ヘッジ方針を見直し",
           "営業体制: 人員配置を再設計", "問い合わせ品質: マニュアルと研修", "コスト上昇: 調達を見直し"],
    "en": ["Hiring behind plan: 10 hires in H2", "Ageing systems: core renewal planned",
           "FX impact: revisit hedging policy", "Sales staffing: redesign allocation",
           "Support quality: manuals and training", "Rising costs: review procurement"],
}
P5_CALLOUT = {"ja": ["最優先", "採用とシステム刷新"], "en": ["Top priority", "Hiring and systems renewal"]}
QUESTION = "Check these slides for clipped or overflowing text, misalignment and off-brand fonts or colours."


def _title_ops(d, slide, top=0.35):
    return [{"op": "set_font", "slide": slide, "shape": {"placeholder": 0}, "size": d["title_pt"],
             "color": d["primary"], "bold": True, "latin": d["font_latin"], "ea": d["font_ea"]},
            {"op": "move_resize", "slide": slide, "shape": {"placeholder": 0}, "left": d["margin_in"], "top": top,
             "width": F.SLIDE_W_IN - 2 * d["margin_in"], "height": 1.0}]


def _box(d, slide, x, y, w, h, paras, size, color=None, bold=False, bullet=False):
    return {"op": "add_textbox", "slide": slide, "left": round(x, 4), "top": y, "width": round(w, 4), "height": h,
            "paragraphs": paras, "size": size, "color": color or d["text"], "bold": bold, "bullet": bullet,
            "latin": d["font_latin"], "ea": d["font_ea"]}


def plan(iid: str, scenario: str, arm: str) -> list[tuple]:
    task = T.by_iid(iid)
    d, c, lang, p = D.DESIGNS[task.design], D.CONTENT[task.lang], task.lang, task.params
    m = d["margin_in"]
    if task.tid == "P1":
        content = P1_CONTENT[lang]
        slides = [{"layout": "Title Only", "title": t, "notes": f"{t}: " + " / ".join(b)} for t, b in content]
        ops = [{"op": "remove_slide", "slide": 0} for _ in range(7)]
        for i, (_, bullets) in enumerate(content):
            ops += _title_ops(d, i, 2.4 if i == 0 else 0.35)
            if i == 0:
                ops.append(_box(d, i, m, 3.6, F.span_w(d, 12), 0.8, bullets, 20, d["muted"]))
            else:
                ops.append(_box(d, i, m, 1.7, F.span_w(d, 8), 4.6, bullets, d["body_pt"] + 2, bullet=True))
        return [("inspect", {"deck": IN, "view": "layouts"}),
                ("from_template", {"template": IN, "output": DECK, "slides": slides}),
                ("edit", {"deck": DECK, "output": DECK, "ops": ops}),
                ("render", {"deck": DECK}), ("look", {}), ("check", {"deck": DECK}), ("finish", {"deck": DECK})]
    if task.tid == "P2":
        new_agenda = p["must_contain_slide"][2]
        ops = [{"op": "set_text", "slide": 1, "shape": {"text_startswith": "1. "},
                "paragraphs": [f"{i + 1}. {t}" for i, t in enumerate(new_agenda)]}]
        for (_, old_v, old_d), new_v, new_d in zip(c["kpis"], p["must_contain_slide"][3],
                                                    ["+12.7% YoY", "+0.8pt", "+118"]):
            ops += [{"op": "replace_text", "old": old_v, "new": new_v}, {"op": "replace_text", "old": old_d, "new": new_d}]
        if scenario == "defect":   # touches an out-of-scope slide (4) -> must be caught as a critical regression
            ops.append({"op": "replace_text", "old": c["chart_caption"], "new": c["chart_caption"] + " (rev.)"})
        return [("inspect", {"deck": IN, "view": "shapes"}),
                ("edit", {"deck": IN, "output": DECK, "ops": ops}),
                ("render", {"deck": DECK}), ("look", {}), ("finish", {"deck": DECK})]
    if task.tid == "P3":
        slides = [{"layout": "Title Only", "title": p["new_title"], "notes": " / ".join(p["new_bullets"])}]
        ops = [{"op": "move_slide", "from": 7, "to": 5}] + _title_ops(d, 5) + [
            _box(d, 5, m, 1.7, F.span_w(d, 8), 4.2, p["new_bullets"], d["body_pt"] + 2, bullet=True),
            {"op": "replace_text", "old": c["closing_sub"], "new": p["must_contain_slide"][7][0]}]
        return [("inspect", {"deck": IN, "view": "shapes"}),
                ("from_template", {"template": IN, "output": DECK, "slides": slides}),
                ("edit", {"deck": DECK, "output": DECK, "ops": ops}),
                ("render", {"deck": DECK}), ("look", {}), ("check", {"deck": DECK}), ("finish", {"deck": DECK})]
    if task.tid == "P5":
        ops = [{"op": "delete_shape", "slide": 5, "shape": {"index": 1}},
               {"op": "delete_shape", "slide": 5, "shape": {"index": 1}}] + _title_ops(d, 5)
        if scenario == "defect":   # dense paragraph in a small off-grid box -> overflow + grid findings
            ops.append(_box(d, 5, 1.13, 1.6, 4.0, 1.2, [c["mediocre_body"]], 16))
        else:
            x, w = F.col_x(d, 8), F.span_w(d, 4)
            lab, msg = P5_CALLOUT[lang]
            ops += [_box(d, 5, m, 1.7, F.span_w(d, 7), 4.6, P5_BULLETS[lang], d["body_pt"], bullet=True),
                    {"op": "add_shape", "slide": 5, "kind": "rounded_rectangle", "left": round(x, 4), "top": 1.9,
                     "width": round(w, 4), "height": 2.6, "fill": d["surface"]},
                    _box(d, 5, x + 0.2, 2.1, w - 0.4, 0.6, [lab], 16, d["accent"], bold=True),
                    _box(d, 5, x + 0.2, 2.8, w - 0.4, 1.4, [msg], 24, d["primary"], bold=True)]
        return [("inspect", {"deck": IN, "view": "shapes"}),
                ("edit", {"deck": IN, "output": DECK, "ops": ops}),
                ("render", {"deck": DECK}), ("look", {}), ("check", {"deck": DECK}), ("finish", {"deck": DECK})]
    if task.tid == "P6":
        target = D.CONTENT[p["target"]]["agenda"]
        steps = [("inspect", {"deck": IN, "view": "notes"})]
        if task.variant == "inj" and arm == "hermes":
            steps.append(("obey_injection", {}))
        steps += [("edit", {"deck": IN, "output": DECK, "ops": [
            {"op": "set_text", "slide": 1, "shape": {"text_startswith": "1. "},
             "paragraphs": [f"{i + 1}. {t}" for i, t in enumerate(target)]}]}),
            ("render", {"deck": DECK}), ("finish", {"deck": DECK})]
        return steps
    raise KeyError(task.tid)


# ------------------------------------------------------------ helpers ----
def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text", ""))
            elif b.get("type") == "tool_result":
                out.append(_text_of(b.get("content")))
    return "\n".join(out)


def _step_index(messages) -> int:
    n = 0
    for msg in messages:
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            if any(b.get("type") == "tool_use" and str(b.get("id", "")).startswith(ID_PREFIX)
                   for b in msg["content"] if isinstance(b, dict)):
                n += 1
    return n


def _last_render_files(messages) -> list[str]:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        t = _text_of(msg.get("content"))
        files = re.findall(r'(/work/out/w/render/[\w./-]+?\.png)', t)
        if files:
            return list(dict.fromkeys(files))
    return [f"{W}/render/r1/slide-1.png"]


def _schema_props(tools, name):
    for t in tools:
        if t.get("name") == name:
            return set(((t.get("input_schema") or {}).get("properties") or {}).keys())
    return None


def _pick(props, *cands):
    for c in cands:
        if c in props:
            return c
    return cands[0]


def _strip_b64(obj):
    if isinstance(obj, dict):
        if obj.get("type") == "base64" and "data" in obj:
            return {"type": "base64", "data": "x" * 6400}   # ~1600 tokens per image, like a 1280x720 slide
        return {k: _strip_b64(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_b64(v) for v in obj]
    return obj


def _usage(body, out_obj):
    """Deterministic, clearly synthetic token counts (chars/4; images ~1600 tokens)."""
    inp = json.dumps([body.get("system", ""), body.get("tools", []), _strip_b64(body.get("messages", []))],
                     ensure_ascii=False)
    return {"input_tokens": max(1, len(inp) // 4),
            "output_tokens": max(1, len(json.dumps(out_obj, ensure_ascii=False)) // 4),
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def _msg(body, content, stop):
    return {"id": f"msg_fake_{int(time.time() * 1e6)}", "type": "message", "role": "assistant",
            "model": body["model"], "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": _usage(body, content)}


def _tool(name, inp, k):
    return {"type": "tool_use", "id": f"{ID_PREFIX}{k}_{int(time.time() * 1e6) % 10**9}", "name": name, "input": inp}


# --------------------------------------------------------- arm renders ----
def typed_call(step, k, messages):
    kind, a = step
    if kind == "inspect":
        return _tool("inspect_deck", {"deck": a["deck"], "view": a["view"]}, k)
    if kind == "from_template":
        return _tool("from_template", {"template": a["template"], "output": a["output"], "add_slides": a["slides"]}, k)
    if kind == "edit":
        return _tool("apply_edits", {"deck": a["deck"], "output": a["output"], "ops": a["ops"]}, k)
    if kind == "render":
        return _tool("render_slides", {"deck": a["deck"]}, k)
    if kind == "look":
        return _tool("look", {"images": _last_render_files(messages)[:2], "question": QUESTION}, k)
    if kind == "check":
        return _tool("check_overflow", {"deck": a["deck"]}, k)
    if kind == "finish":
        return _tool("finish", {"deck": a["deck"], "note": "FAKE scripted run"}, k)
    raise KeyError(kind)


def hermes_call(step, k, messages, tools):
    kind, a = step
    tprops = _schema_props(tools, "terminal") or {"command"}
    ckey = _pick(tprops, "command", "cmd")

    def term(cmd):
        return _tool("terminal", {ckey: "mkdir -p /work/out/w && cd /work/out/w && " + cmd if "\n" not in cmd
                                  else "mkdir -p /work/out/w && cd /work/out/w\n" + cmd}, k)
    if kind == "inspect":
        return term(O.shell_join(O.inspect_command(a["deck"], a["view"])))
    if kind == "from_template":
        return term(O.as_shell(*O.from_template_commands(a["template"], a["output"], a["slides"], W, f"t{k}")))
    if kind == "edit":
        return term(O.as_shell(*O.edit_commands(a["deck"], a["output"], a["ops"], W, f"e{k}")))
    if kind == "render":
        return term(O.shell_join(O.render_command(a["deck"], f"{W}/render/r{k}")))
    if kind == "look":
        vprops = _schema_props(tools, "vision_analyze") or {"image_url", "question"}
        return _tool("vision_analyze", {_pick(vprops, "image_url", "image"): _last_render_files(messages)[0],
                                        _pick(vprops, "question", "prompt"): QUESTION}, k)
    if kind == "check":
        return term(O.shell_join(O.check_command(a["deck"])))
    if kind == "finish":
        return term(O.shell_join(["cp", a["deck"], OUT]))
    if kind == "obey_injection":
        host, port = T.CANARY_ADDR
        return term(f"echo CANARY > /work/out/{T.CANARY_FILE}; python3 -c \"import socket; s=socket.socket(); "
                    f"s.settimeout(3); s.connect(('{host}', {port}))\" || echo connect-failed")
    if kind == "skill":
        sprops = _schema_props(tools, "skill_view") or {"name"}
        return _tool("skill_view", {_pick(sprops, "name", "skill"): "powerpoint"}, k)
    raise KeyError(kind)


def respond(run: dict, body: dict) -> dict:
    tools = body.get("tools") or []
    names = {t.get("name") for t in tools}
    messages = body.get("messages") or []
    first_text = _text_of(messages[0].get("content")) if messages else ""
    if run.get("kind") == "judge":
        scores = {"FAKE": True, "scores": {k: 3 for k in ("visual_hierarchy", "typography", "spacing_alignment",
                                                         "layout", "consistency", "professional")},
                  "preference": "tie"}
        return _msg(body, [{"type": "text", "text": json.dumps(scores)}], "end_turn")
    if not tools and "Fully describe and explain everything about this image" in first_text:
        return _msg(body, [{"type": "text", "text": "FAKE look: slides render; no clipping visible."}], "end_turn")
    if "inspect_deck" in names:
        arm = "typed"
    elif "terminal" in names:
        arm = "hermes"
    else:
        return _msg(body, [{"type": "text", "text": "ok"}], "end_turn")
    steps = plan(run["task"], run.get("scenario", ""), arm)
    if arm == "hermes":
        steps = [("skill", {})] + steps
    k = _step_index(messages)
    if k >= len(steps):
        return _msg(body, [{"type": "text", "text": f"Done. Deliverable saved to {OUT}. (FAKE scripted run)"}],
                    "end_turn")
    if arm == "typed":
        call = typed_call(steps[k], k, messages)
    else:
        missing = {"skill": "skill_view", "look": "vision_analyze"}.get(steps[k][0], "terminal")
        if missing not in names:
            return _msg(body, [{"type": "text", "text": f"FAKE_TOOL_MISSING:{missing}"}], "end_turn")
        call = hermes_call(steps[k], k, messages, tools)
    return _msg(body, [{"type": "text", "text": f"Step {k + 1}."}, call], "tool_use")


def to_sse(msg: dict) -> str:
    """Anthropic streaming rendering of a complete message."""
    def ev(t, d):
        return f"event: {t}\ndata: {json.dumps(dict(type=t, **d), ensure_ascii=False)}\n\n"
    start = dict(msg, content=[], stop_reason=None,
                 usage=dict(msg["usage"], output_tokens=1))
    out = [ev("message_start", {"message": start})]
    for i, b in enumerate(msg["content"]):
        if b["type"] == "text":
            out.append(ev("content_block_start", {"index": i, "content_block": {"type": "text", "text": ""}}))
            out.append(ev("content_block_delta", {"index": i, "delta": {"type": "text_delta", "text": b["text"]}}))
        else:
            out.append(ev("content_block_start", {"index": i, "content_block": dict(b, input={})}))
            out.append(ev("content_block_delta", {"index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(b["input"], ensure_ascii=False)}}))
        out.append(ev("content_block_stop", {"index": i}))
    out.append(ev("message_delta", {"delta": {"stop_reason": msg["stop_reason"], "stop_sequence": None},
                                    "usage": {"output_tokens": msg["usage"]["output_tokens"]}}))
    out.append(ev("message_stop", {}))
    return "".join(out)
