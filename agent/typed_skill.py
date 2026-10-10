"""Arm B's instruction text: the pinned powerpoint SKILL.md with script
invocations mechanically rewritten to typed tool names, plus the parity
helper appendix. The rewrite table below is the complete, reviewable diff
rule; `python3 -m agent.typed_skill --diff` prints the resulting diff.
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

SKILL_MD = Path("/opt/hermes/skills/productivity/powerpoint/SKILL.md")
APPENDIX = Path(__file__).resolve().parent / "skill_appendix.md"

# (regex, replacement) applied in order, line by line. Nothing else is changed.
REWRITES = [
    (r"`python scripts/pptx_inspect\.py [^`]*`", "`inspect_deck(deck, view=\"shapes|layouts|theme\")`"),
    (r"`python scripts/pptx_shape_ops\.py [^`]*`", "`apply_edits(deck, output, ops)` shape ops"),
    (r"; see `--help`\.", "."),
    (r"`python scripts/pptx_check\.py deck\.pptx --overflow` or `--validate`", "`check_overflow(deck)` or `validate_deck(deck)`"),
    (r"python scripts/pptx_create\.py [^`|\n]*", "create_deck(spec, output)"),
    (r"python scripts/pptx_read\.py [^`|\n]*--outline[^`|\n]*", "inspect_deck(deck, view=\"outline\")"),
    (r"python scripts/pptx_read\.py [^`|\n]*--notes[^`|\n]*", "inspect_deck(deck, view=\"notes\")"),
    (r"python scripts/pptx_read\.py [^`|\n]*--images[^`|\n]*", "(image export is not offered as a typed tool)"),
    (r"python scripts/pptx_edit\.py [^`|\n]*--chart-data[^`|\n]*", "apply_edits(ops=[{\"op\": \"chart_data\", ...}])"),
    (r"python scripts/pptx_edit\.py [^`|\n]*--replace-text[^`|\n]*", "apply_edits(ops=[{\"op\": \"replace_text\", ...}])"),
    (r"python scripts/pptx_edit\.py [^`|\n]*", "apply_edits(deck, output, ops)"),
    (r"python scripts/pptx_from_template\.py [^`|\n]*", "from_template(template, output, tokens, add_slides)"),
    (r"python scripts/pptx_render\.py [^`|\n]*", "render_slides(deck)"),
    (r"`pptx_create\.py[^`]*`", "`create_deck`"),
    (r"`pptx_read\.py[^`]*`", "`inspect_deck`"),
    (r"`pptx_edit\.py[^`]*`", "`apply_edits`"),
    (r"`pptx_from_template\.py[^`]*`", "`from_template`"),
    (r"`pptx_render\.py[^`]*`", "`render_slides`"),
    (r"`vision_analyze`", "`look`"),
    (r"with `terminal`", "with the typed tools"),
    (r"via `terminal`", "via the typed tools"),
    (r"Author JSON specs with `write_file`; inspect script output and generated\s*", "Pass JSON specs as tool arguments; "),
    (r"JSON with `read_file`\.", "tool results are JSON."),
]


def rewrite(text: str) -> str:
    out = []
    for line in text.splitlines():
        for pat, rep in REWRITES:
            line = re.sub(pat, rep, line)
        out.append(line)
    return "\n".join(out) + "\n"


def system_prompt(skill_md: Path = SKILL_MD) -> str:
    body = rewrite(skill_md.read_text(encoding="utf-8"))
    # The image's SKILL.md already carries the parity-helper appendix (agent/skill_appendix.md),
    # so both arms read the same appendix; it is rewritten like the rest.
    # H can run `--help` on any script; B gets the same help text (module docstrings) inline.
    helps = []
    for name, tool in (("pptx_create.py", "create_deck"), ("pptx_edit.py", "apply_edits"),
                       ("pptx_from_template.py", "from_template"), ("pptx_shape_ops.py", "apply_edits shape ops")):
        doc = _docstring(skill_md.parent / "scripts" / name)
        if doc:
            helps.append(f"### Help text of the script behind `{tool}`\n\n{doc.strip()}\n")
    return ("You are an agent that produces PowerPoint deliverables using the typed tools provided. "
            "Follow this skill:\n\n" + body + "\n" + "\n".join(helps))


def _docstring(path: Path) -> str:
    import ast
    try:
        return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    except (OSError, SyntaxError):
        return ""


def diff(skill_md: Path = SKILL_MD) -> str:
    a = skill_md.read_text(encoding="utf-8")
    return "".join(difflib.unified_diff(a.splitlines(True), rewrite(a).splitlines(True), "SKILL.md",
                                        "SKILL.md (typed-tools rewrite)"))


if __name__ == "__main__":
    p = Path(sys.argv[2]) if len(sys.argv) > 2 else SKILL_MD
    print(diff(p) if "--diff" in sys.argv else system_prompt(p))
