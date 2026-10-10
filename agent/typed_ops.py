"""Pure mapping from typed-tool requests to argument arrays for the pinned
skill scripts (no shell, no python-pptx import). Used by the typed-tool arm at
runtime and by the fake model to script the same operations for the Hermes
arm, so both arms run byte-identical script invocations in fake mode.

Slide indexes are 0-based everywhere, as in the skill scripts.
"""
from __future__ import annotations

import json

SKILL_SCRIPTS = "/opt/hermes/skills/productivity/powerpoint/scripts"
PY = "python3"

EDIT_OPS = {  # op -> (pptx_edit.py flag, argument keys)
    "replace_text": ("--replace-text", ["old", "new"]),
    "set_notes": ("--set-notes", ["slide", "text"]),
    "append_notes": ("--append-notes", ["slide", "text"]),
    "remove_slide": ("--remove-slide", ["slide"]),
    "move_slide": ("--move-slide", ["from", "to"]),
    "duplicate_slide": ("--duplicate-slide", ["slide"]),
    "set_background": ("--set-background", ["slide", "color"]),
    "set_footer": ("--set-footer", ["slide", "text"]),
    "enable_slide_number": ("--enable-slide-number", ["slide"]),
}
SHAPE_OPS = {"set_text", "set_font", "set_fill", "move_resize", "add_textbox", "add_shape", "delete_shape",
             "table_add_row", "table_set_cell"}
ALL_OPS = set(EDIT_OPS) | SHAPE_OPS | {"chart_data"}


def script(name: str) -> str:
    return f"{SKILL_SCRIPTS}/{name}"


def edit_commands(deck: str, output: str, ops: list[dict], wdir: str, tag: str = "e") -> tuple[list[list[str]], dict]:
    """-> (argv list, {path: text} files to write first). Applied to a temp copy, then moved
    over `output` only if every command succeeded (the caller stops at the first failure)."""
    if not isinstance(ops, list) or not ops:
        raise ValueError("ops must be a non-empty list")
    tmp = f"{wdir}/.{tag}.tmp.pptx"
    cmds, files = [["cp", deck, tmp]], {}
    group: list[dict] = []

    def flush(k):
        if group:
            p = f"{wdir}/.{tag}.shape{k}.json"
            files[p] = json.dumps(list(group), ensure_ascii=False)
            cmds.append([PY, script("pptx_shape_ops.py"), tmp, "--ops", p])
            group.clear()

    for i, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") not in ALL_OPS:
            raise ValueError(f"op {i}: unknown op {op.get('op') if isinstance(op, dict) else op!r}")
        name = op["op"]
        if name in SHAPE_OPS:
            group.append(op)
            continue
        flush(i)
        if name == "chart_data":
            p = f"{wdir}/.{tag}.chart{i}.json"
            spec = {k: v for k, v in op.items() if k != "op"}
            files[p] = json.dumps(spec, ensure_ascii=False)
            cmds.append([PY, script("pptx_edit.py"), tmp, "--chart-data", p])
            continue
        flag, keys = EDIT_OPS[name]
        missing = [k for k in keys if k not in op]
        if missing:
            raise ValueError(f"op {i} ({name}): missing {missing}")
        cmds.append([PY, script("pptx_edit.py"), tmp, flag, *[str(op[k]) for k in keys]])
    flush(len(ops))
    cmds.append(["mv", tmp, output])
    return cmds, files


def from_template_commands(template: str, output: str, slides: list[dict], wdir: str, tag: str = "t"):
    p = f"{wdir}/.{tag}.slides.json"
    return [[PY, script("pptx_from_template.py"), template, output, "--add-slides", p]], {
        p: json.dumps({"slides": slides}, ensure_ascii=False)}


def create_commands(spec: dict, output: str, wdir: str, tag: str = "c"):
    p = f"{wdir}/.{tag}.spec.json"
    return [[PY, script("pptx_create.py"), p, output]], {p: json.dumps(spec, ensure_ascii=False)}


def inspect_command(deck: str, view: str) -> list[str]:
    if view in ("outline", "notes"):
        return [PY, script("pptx_read.py"), deck, "--" + view]
    if view in ("shapes", "layouts", "theme"):
        return [PY, script("pptx_inspect.py"), deck, "--view", view]
    raise ValueError(f"unknown view {view!r}")


def render_command(deck: str, outdir: str) -> list[str]:
    return [PY, script("pptx_render.py"), deck, "--outdir", outdir]


def check_command(deck: str) -> list[str]:
    return [PY, script("pptx_check.py"), deck, "--overflow"]


def validate_command(deck: str) -> list[str]:
    return [PY, script("pptx_check.py"), deck, "--validate"]


def shell_join(argv: list[str]) -> str:
    import shlex
    return " ".join(shlex.quote(a) for a in argv)


def as_shell(cmds: list[list[str]], files: dict) -> str:
    """The same operations as one POSIX shell snippet (Hermes terminal, fake mode only)."""
    parts = []
    for p, text in files.items():
        marker = "PILOT_EOF"
        parts.append(f"cat > {p} <<'{marker}'\n{text}\n{marker}")
    parts.append(" && ".join(shell_join(c) for c in cmds))
    return "\n".join(parts)
