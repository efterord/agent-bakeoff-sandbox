"""Fake plans, typed-op mapping, scan helpers, screening rules (no Docker)."""
import json
import os
import shlex

import pytest

from agent import typed_ops as O
from gate import fake_model as FM
from pilot import report as R
from pptxbench import scan
from pptxbench import tasks as T

ALL = T.PILOT_LEAN + [T.INJECTION_CANARY]


@pytest.mark.parametrize("iid", ALL)
@pytest.mark.parametrize("arm", ["typed", "hermes"])
@pytest.mark.parametrize("scenario", ["", "defect"])
def test_plans_render_for_both_arms(iid, arm, scenario):
    steps = FM.plan(iid, scenario, arm)
    assert steps[-1][0] == "finish"
    for k, step in enumerate(steps):
        if arm == "typed":
            call = FM.typed_call(step, k, [])
            assert call["type"] == "tool_use" and call["id"].startswith(FM.ID_PREFIX)
        else:
            tools = [{"name": "terminal", "input_schema": {"properties": {"command": {}}}},
                     {"name": "vision_analyze", "input_schema": {"properties": {"image_url": {}, "question": {}}}}]
            call = FM.hermes_call(step, k, [], tools)
            if call["name"] == "terminal":
                assert "/opt/hermes/skills/productivity/powerpoint/scripts" in call["input"]["command"] or \
                    step[0] in ("finish", "obey_injection")


def test_same_script_invocations_in_both_arms():
    a = FM.plan("P2-sakura-ja", "", "typed")
    edit = [s for s in a if s[0] == "edit"][0][1]
    cmds, files = O.edit_commands(edit["deck"], edit["output"], edit["ops"], FM.W, "e1")
    sh = O.as_shell(cmds, files)
    for argv in cmds:
        assert O.shell_join(argv) in sh
    assert cmds[0][0] == "cp" and cmds[-1][0] == "mv"


def test_injection_only_obeyed_by_hermes_fake():
    assert any(s[0] == "obey_injection" for s in FM.plan(T.INJECTION_CANARY, "", "hermes"))
    assert not any(s[0] == "obey_injection" for s in FM.plan(T.INJECTION_CANARY, "", "typed"))


def test_edit_ops_validation():
    with pytest.raises(ValueError):
        O.edit_commands("a", "b", [{"op": "rm_rf"}], "/w")
    with pytest.raises(ValueError):
        O.edit_commands("a", "b", [{"op": "replace_text", "old": "x"}], "/w")
    cmds, _ = O.edit_commands("a", "b", [{"op": "set_text", "slide": 0, "shape": {"index": 0}, "paragraphs": ["x"]},
                                         {"op": "remove_slide", "slide": 1},
                                         {"op": "set_fill", "slide": 0, "shape": {"index": 0}, "color": "FFFFFF"}], "/w")
    scripts = [c[1].rsplit("/", 1)[-1] for c in cmds if c[0] == O.PY]
    assert scripts == ["pptx_shape_ops.py", "pptx_edit.py", "pptx_shape_ops.py"]


def test_shell_rendering_quotes_untrusted_text():
    cmd = O.shell_join(["echo", "a'b; rm -rf /"])
    assert shlex.split(cmd) == ["echo", "a'b; rm -rf /"]


def test_tasks_lean_set():
    ts = [T.by_iid(i) for i in T.PILOT_LEAN]
    assert {t.tid for t in ts} == {"P1", "P2", "P3", "P5"}
    assert sum(t.lang == "ja" for t in ts) >= 1
    c = T.by_iid(T.INJECTION_CANARY)
    assert c.variant == "inj" and "203.0.113.7" in T.INJECTION_NOTE


def test_zip_guard(tmp_path):
    bomb = scan.make_zip_bomb(tmp_path / "b.pptx", mb=8)
    assert any("ratio" in f for f in scan.zip_guard(bomb))
    trav = scan.make_traversal_zip(tmp_path / "t.pptx")
    assert any("traversal" in f for f in scan.zip_guard(trav))
    (tmp_path / "n.pptx").write_bytes(b"not a zip")
    assert scan.zip_guard(tmp_path / "n.pptx") == ["not a zip (bad magic)"]


def test_safe_extract_refuses_specials(tmp_path):
    out = tmp_path / "out"
    (out / "trace").mkdir(parents=True)
    os.symlink("/etc/passwd", out / "deliverable.pptx")
    (out / "x.txt").write_text("x")
    os.link(out / "x.txt", out / "canary.txt")
    os.mkfifo(out / "trace" / "events.jsonl")
    (out / "trace" / "ok.jsonl").write_text('{"a": 1}\n')
    ex = scan.safe_extract(out, tmp_path / "dest")
    r = {x["name"]: x["reason"] for x in ex["refused"]}
    assert r["deliverable.pptx"] == "symlink" and r["canary.txt"] == "hard link"
    assert r["trace/events.jsonl"] == "fifo" and "x.txt" in r
    assert ex["copied"] == ["trace/ok.jsonl"] and not (tmp_path / "dest" / "deliverable.pptx").exists()


def test_screening_language_never_claims_equivalence():
    o = R.screening_outcome(0.5, (-3.0, 4.0), 2, 4, 8, 8, 0.5, 0.2, 0.2)
    assert "NOT evidence of equivalence" in o
    assert "Hermes materially better" in R.screening_outcome(18, (6, 30), 4, 4, 8, 6, 0.8, 0.3, 0.2)
    assert "typed tools materially better" in R.screening_outcome(-15, (-27, -3), 0, 4, 6, 8, 0.2, 0.3, 0.2)


def test_sign_test():
    assert abs(R.sign_test_p(6, 6) - 0.03125) < 1e-9
    assert R.sign_test_p(3, 4) == 0.625


def test_identifying_string_flags():
    assert scan.identifying_strings("made with Hermes and pptx_shape_ops") == ["Hermes", "pptx_shape_ops"]
