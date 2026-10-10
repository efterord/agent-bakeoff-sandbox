"""Arm B: typed-tool agent loop (no shell), run inside the sandbox container.

The model sees the pinned powerpoint SKILL.md with its script invocations
mechanically rewritten to tool names (agent/typed_skill.py), plus the same
task wrapper text as the Hermes arm. Every tool is JSON in / JSON out and
calls the unmodified skill scripts (or the parity helpers) as subprocesses
with argument arrays. Paths are confined to /work/in (read) and /work/out/w
(read/write). The model is reached only through the in-container forwarder
-> per-run gate socket.

    python3 -m agent.typed_driver        (reads /work/in/task.json)
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import forwarder  # noqa: E402
from agent import typed_ops as O  # noqa: E402
from agent import typed_skill  # noqa: E402

IN, OUTD, W = Path("/work/in"), Path("/work/out"), Path("/work/out/w")
TRACE = OUTD / "trace"
MODEL = "claude-haiku-4-5-20251001"   # informational; the gate overwrites it
MAX_TOOL_CALLS = 40
MAX_LOOK_IMAGES = 10
TOOL_TIMEOUT = 240
VISION_TEMPLATE = ("Fully describe and explain everything about this image, then answer the "
                   "following question:\n\n{question}")   # Hermes vision_analyze legacy prompt, verbatim

_deck = {"type": "string", "description": "Path to a .pptx under /work/in or /work/out/w"}
_out = {"type": "string", "description": "Output .pptx path under /work/out/w"}
TOOLS = [
    {"name": "inspect_deck", "description": "Read a deck. view=outline|notes (pptx_read.py) or "
     "shapes|layouts|theme (pptx_inspect.py: slide ids, shape ids, geometry in inches, fonts, colours).",
     "input_schema": {"type": "object", "properties": {"deck": _deck, "view": {
         "type": "string", "enum": ["outline", "notes", "shapes", "layouts", "theme"]}}, "required": ["deck", "view"]}},
    {"name": "create_deck", "description": "Create a new deck from a JSON spec (pptx_create.py spec format).",
     "input_schema": {"type": "object", "properties": {"spec": {"type": "object"}, "output": _out},
                      "required": ["spec", "output"]}},
    {"name": "from_template", "description": "Fill {{tokens}} in a template deck and/or append slides that use "
     "the template's own layouts (pptx_from_template.py). add_slides items: {layout, title, bullets[], notes}.",
     "input_schema": {"type": "object", "properties": {
         "template": _deck, "output": _out, "tokens": {"type": "object"},
         "add_slides": {"type": "array", "items": {"type": "object"}}}, "required": ["template", "output"]}},
    {"name": "add_slide", "description": "Insert one slide after slide index `after` (0-based) using the layout of "
     "slide `layout_of_slide` or the layout named `layout_name`; with title, bullets and notes.",
     "input_schema": {"type": "object", "properties": {
         "deck": _deck, "output": _out, "after": {"type": "integer"}, "layout_of_slide": {"type": "integer"},
         "layout_name": {"type": "string"}, "title": {"type": "string"},
         "bullets": {"type": "array", "items": {"type": "string"}}, "notes": {"type": "string"}},
         "required": ["deck", "output", "after", "title"]}},
    {"name": "apply_edits", "description": "Apply ops atomically to a copy of `deck`, saved as `output`. Slide "
     "indexes are 0-based. Slide/text ops (pptx_edit.py): replace_text{old,new}, set_notes{slide,text}, "
     "append_notes{slide,text}, remove_slide{slide}, move_slide{from,to}, duplicate_slide{slide}, "
     "set_background{slide,color}, set_footer{slide,text}, enable_slide_number{slide}, chart_data{slide,chart,"
     "categories?,series?|ops?}. Shape ops (pptx_shape_ops.py; shape selector {id|name|index|text|"
     "text_startswith|placeholder}; inches; RRGGBB): set_text{slide,shape,paragraphs[]}, set_font{slide,shape,"
     "latin?,ea?,size?,color?,bold?}, set_fill{slide,shape,color}, move_resize{slide,shape,left?,top?,width?,"
     "height?}, add_textbox{slide,left,top,width,height,paragraphs[],size,color?,bold?,latin?,ea?,bullet?}, "
     "add_shape{slide,kind,left,top,width,height,fill}, delete_shape{slide,shape}, table_add_row{slide,shape,"
     "values[]}, table_set_cell{slide,shape,row,col,text}. On failure nothing is written and the failing "
     "command is reported.",
     "input_schema": {"type": "object", "properties": {"deck": _deck, "output": _out, "ops": {
         "type": "array", "items": {"type": "object"}}}, "required": ["deck", "output", "ops"]}},
    {"name": "render_slides", "description": "Render a deck to 1280x720 PNGs (pptx_render.py via LibreOffice; at "
     "most 4 renders per run, 10 slides each). Returns the image paths.",
     "input_schema": {"type": "object", "properties": {"deck": _deck, "slides": {
         "type": "array", "items": {"type": "integer"}}}, "required": ["deck"]}},
    {"name": "look", "description": "Look at rendered slide images (<= 10) and answer a question about them.",
     "input_schema": {"type": "object", "properties": {"images": {"type": "array", "items": {"type": "string"}},
                                                       "question": {"type": "string"}},
                      "required": ["images", "question"]}},
    {"name": "check_overflow", "description": "Estimate text overflow and out-of-bounds shapes per slide.",
     "input_schema": {"type": "object", "properties": {"deck": _deck}, "required": ["deck"]}},
    {"name": "validate_deck", "description": "Package validity and security findings (macros, external links).",
     "input_schema": {"type": "object", "properties": {"deck": _deck}, "required": ["deck"]}},
    {"name": "finish", "description": "Deliver `deck` as the final result and end the run.",
     "input_schema": {"type": "object", "properties": {"deck": _deck, "note": {"type": "string"}},
                      "required": ["deck"]}},
]


class ToolError(Exception):
    pass


def _path(p, write=False) -> str:
    rp = os.path.realpath(str(p))
    roots = [str(W)] if write else [str(W), str(IN)]
    if not any(rp == r or rp.startswith(r + "/") for r in roots):
        raise ToolError(f"path {p!r} is outside the allowed directories ({', '.join(roots)})")
    if not rp.endswith(".pptx") and not write:
        pass
    return rp


def _run(argv, timeout=TOOL_TIMEOUT) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=str(W),
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s", "argv0": Path(argv[1] if len(argv) > 1 else argv[0]).name}
    return {"ok": r.returncode == 0, "rc": r.returncode, "stdout": r.stdout[-30000:], "stderr": r.stderr[-4000:],
            "secs": round(time.time() - t0, 2), "script": Path(argv[1] if argv[0].startswith("python") else argv[0]).name}


def _run_seq(cmds, files) -> dict:
    for p, text in files.items():
        Path(p).write_text(text, encoding="utf-8")
    results = []
    for argv in cmds:
        if argv[0] == "cp":
            shutil.copyfile(argv[1], argv[2])
            continue
        if argv[0] == "mv":
            os.replace(argv[1], argv[2])
            continue
        r = _run(argv)
        results.append({k: r.get(k) for k in ("script", "ok", "rc", "stdout", "stderr")})
        if not r["ok"]:
            return {"ok": False, "failed_command": len(results) - 1, "results": results}
    return {"ok": True, "results": results}


class Driver:
    def __init__(self, task: dict):
        self.task = task
        self.calls = 0
        self.renders = 0
        self.finished = None
        self.events = []
        self.requests = 0
        TRACE.mkdir(parents=True, exist_ok=True)
        W.mkdir(parents=True, exist_ok=True)

    def log(self, **ev):
        ev["t"] = round(time.time(), 3)
        self.events.append(ev)
        with open(TRACE / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def post(self, body) -> tuple[int, dict]:
        self.requests += 1
        conn = http.client.HTTPConnection("127.0.0.1", forwarder.PORT, timeout=600)
        conn.request("POST", "/v1/messages", body=json.dumps(body).encode(),
                     headers={"content-type": "application/json", "x-api-key": "sandbox-no-key",
                              "anthropic-version": "2023-06-01"})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, {"type": "error", "error": {"type": "bad_gateway_body", "message": data[:200].decode(
                "utf-8", "replace")}}

    # ---------------------------------------------------------- tools ----
    def tool(self, name, a) -> dict:
        if name == "inspect_deck":
            return _run(O.inspect_command(_path(a["deck"]), a["view"]))
        if name == "create_deck":
            return _run_seq(*O.create_commands(a["spec"], _path(a["output"], True), str(W), f"c{self.calls}"))
        if name == "from_template":
            tmpl, out = _path(a["template"]), _path(a["output"], True)
            cmds, files = O.from_template_commands(tmpl, out, a.get("add_slides") or [], str(W), f"t{self.calls}")
            if a.get("tokens"):
                vp = f"{W}/.t{self.calls}.values.json"
                files[vp] = json.dumps(a["tokens"], ensure_ascii=False)
                cmds[0] += ["--values", vp]
            return _run_seq(cmds, files)
        if name == "add_slide":
            deck, out = _path(a["deck"]), _path(a["output"], True)
            layout = a.get("layout_name")
            if layout is None:
                info = _run(O.inspect_command(deck, "shapes"))
                try:
                    layout = json.loads(info["stdout"])["slides"][int(a.get("layout_of_slide", a["after"]))]["layout"]
                except Exception as e:
                    raise ToolError(f"cannot resolve layout: {e}")
            spec = [{"layout": layout, "title": a["title"], "bullets": a.get("bullets") or [], "notes": a.get("notes", "")}]
            tmp = f"{W}/.add{self.calls}.pptx"
            cmds, files = O.from_template_commands(deck, tmp, spec, str(W), f"a{self.calls}")
            r = _run_seq(cmds, files)
            if not r["ok"]:
                return r
            outline = json.loads(_run(O.inspect_command(tmp, "shapes"))["stdout"])
            last = len(outline["slides"]) - 1
            c2, f2 = O.edit_commands(tmp, out, [{"op": "move_slide", "from": last, "to": int(a["after"]) + 1}],
                                     str(W), f"m{self.calls}")
            return _run_seq(c2, f2)
        if name == "apply_edits":
            try:
                cmds, files = O.edit_commands(_path(a["deck"]), _path(a["output"], True), a["ops"], str(W),
                                              f"e{self.calls}")
            except ValueError as e:
                raise ToolError(str(e))
            return _run_seq(cmds, files)
        if name == "render_slides":
            self.renders += 1
            outdir = f"{W}/render/r{self.calls}"
            r = _run(O.render_command(_path(a["deck"]), outdir))
            try:
                files = json.loads(r["stdout"]).get("files") or []
            except (ValueError, KeyError):
                files = []
            if a.get("slides"):
                files = [f for i, f in enumerate(files, 1) if i in set(a["slides"])]
            return {"ok": r["ok"] and bool(files), "files": files, "stderr": r.get("stderr", "")[-800:]}
        if name == "look":
            imgs = [_path(p) for p in (a.get("images") or [])][:MAX_LOOK_IMAGES]
            if not imgs:
                raise ToolError("no images")
            content = []
            for p in imgs:
                if not p.endswith(".png") or not p.startswith(f"{W}/render/"):
                    raise ToolError(f"look accepts rendered PNGs under {W}/render only")
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                            "data": base64.b64encode(Path(p).read_bytes()).decode()}})
            content.append({"type": "text", "text": VISION_TEMPLATE.format(question=a.get("question", ""))})
            st, resp = self.post({"model": MODEL, "max_tokens": 2048,
                                  "messages": [{"role": "user", "content": content}]})
            if st != 200:
                return {"ok": False, "error": resp.get("error")}
            return {"ok": True, "analysis": "".join(b.get("text", "") for b in resp.get("content", [])
                                                    if b.get("type") == "text")}
        if name == "check_overflow":
            return _run(O.check_command(_path(a["deck"])))
        if name == "validate_deck":
            return _run(O.validate_command(_path(a["deck"])))
        if name == "finish":
            src = _path(a["deck"])
            if not os.path.isfile(src):
                raise ToolError("deck does not exist")
            shutil.copyfile(src, OUTD / "deliverable.pptx")
            self.finished = {"deck": src, "note": str(a.get("note", ""))[:500]}
            return {"ok": True, "delivered": str(OUTD / "deliverable.pptx")}
        raise ToolError(f"unknown tool {name}")

    # ----------------------------------------------------------- loop ----
    def run(self) -> dict:
        system = typed_skill.system_prompt()
        messages = [{"role": "user", "content": self.task["prompt"]}]
        status = "max_tool_calls"
        while self.calls < MAX_TOOL_CALLS:
            st, resp = self.post({"model": MODEL, "max_tokens": 8192, "system": system, "tools": TOOLS,
                                  "messages": messages})
            if st != 200 or resp.get("type") == "error":
                et = (resp.get("error") or {}).get("type")
                self.log(event="model_error", status=st, error=resp.get("error"))
                status = "budget_exhausted" if et == "budget_exhausted" else f"model_error:{st}:{et}"
                break
            content = resp.get("content") or []
            messages.append({"role": "assistant", "content": content})
            uses = [b for b in content if b.get("type") == "tool_use"]
            if not uses:
                status = "end_turn"
                break
            results = []
            for u in uses:
                self.calls += 1
                t0 = time.time()
                try:
                    out = self.tool(u["name"], u.get("input") or {})
                    err = not out.get("ok", False)
                except (ToolError, KeyError, TypeError, ValueError, OSError) as e:
                    out, err = {"ok": False, "error": f"{type(e).__name__}: {e}"}, True
                text = json.dumps(out, ensure_ascii=False)[:40000]
                self.log(event="tool", n=self.calls, tool=u["name"], ok=not err, secs=round(time.time() - t0, 2),
                         input_sha256=hashlib.sha256(json.dumps(u.get("input"), sort_keys=True).encode()).hexdigest(),
                         result_bytes=len(text), detail=(out.get("error") or out.get("stderr") or "")[:300]
                         if err else "")
                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": text, "is_error": err})
            messages.append({"role": "user", "content": results})
            if self.finished:
                status = "finished"
                break
        return {"arm": "typed-tools", "status": status, "tool_calls": self.calls, "renders": self.renders,
                "model_requests": self.requests, "finished": bool(self.finished)}


def main():
    task = json.loads((IN / "task.json").read_text(encoding="utf-8"))
    forwarder.start()
    d = Driver(task)
    t0 = time.time()
    try:
        summary = d.run()
    except Exception as e:  # recorded, never hidden
        summary = {"arm": "typed-tools", "status": f"driver_crash:{type(e).__name__}:{e}"[:300]}
    summary["wall_s"] = round(time.time() - t0, 1)
    (OUTD / "driver.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
