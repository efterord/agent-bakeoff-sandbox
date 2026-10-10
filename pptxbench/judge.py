"""Visual rubric and judge interface.

The model judge (pilot/judge.py) runs behind the gate with a pinned model and
a frozen, fingerprinted prompt; it sees rendered PNGs only. `FakeJudge` returns
fixed scores and labels every result FAKE.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

RUBRIC_VERSION = "deliverables-visual-v1"

# 1-5 anchors per dimension. 1 = unacceptable, 3 = acceptable but plainly
# machine-made, 5 = indistinguishable from a skilled human designer's work.
RUBRIC = {
    "visual_hierarchy": "Is the most important element obvious; do title, key figure and body read in order?",
    "typography": "Font families, sizes and weights consistent and on-brand; no fallback/tofu glyphs; CJK set properly.",
    "spacing_alignment": "Consistent margins, gutters and grid alignment; no crowding or accidental near-misses.",
    "layout": "Content fills the canvas purposefully; balance; nothing cramped or floating.",
    "consistency": "Slides/pages share one visual language (colours, title positions, bullet styles).",
    "chart_table_quality": "Charts/tables legible, labelled, correctly scaled, styled with the palette.",
    "image_handling": "Images/shapes crisp, not distorted, cropped or overlapping text. N/A -> 3.",
    "overflow": "No clipped, overflowing or overlapping text anywhere (5 = none, 1 = several).",
    "design_preservation": "Edit tasks: untouched content looks identical; edited parts match the existing design.",
    "edit_precision": "Edit tasks: exactly the requested change, nothing more; create tasks: brief fully covered.",
    "professional": "Would a professional send this to an executive without rework?",
}

CALIBRATION = [
    # (fixture-relative render, expected band [lo, hi] for the mean 1-5 score)
    ("deck:slide3", (3.5, 5.0)),   # KPI slide, on-grid and on-brand
    ("deck:slide4", (3.5, 5.0)),   # native chart, palette colours
    ("deck:slide6", (1.0, 2.5)),   # the deliberately mediocre slide
    ("doc:page1", (3.0, 5.0)),
]


@dataclass
class JudgeResult:
    scores: dict
    mean: float          # 1-5
    normalized: float    # 0-1
    label: str
    rationale: str = ""


class FakeJudge:
    """Deterministic stub: every dimension scores 3. Labelled FAKE."""
    label = "FAKE-JUDGE (fixed scores, not a measurement)"
    model = "none"

    def score(self, task, images: list[Path], context: dict) -> JudgeResult:
        scores = {k: 3 for k in RUBRIC}
        return JudgeResult(scores, 3.0, 0.5, self.label, "fixed stub")

    def calibrate(self, items) -> dict:
        return {"status": "not-applicable-in-fake-mode", "items": len(items)}


def prompt_fingerprint() -> str:
    """Hash of the rubric text: the judge prompt is frozen per bake-off run."""
    h = hashlib.sha256(RUBRIC_VERSION.encode())
    for k in sorted(RUBRIC):
        h.update(k.encode() + RUBRIC[k].encode())
    return h.hexdigest()[:16]
