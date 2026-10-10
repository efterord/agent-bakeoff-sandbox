"""Scoring and the pre-registered decision rule (pure Python, no deps).

Combined quality score Q (0-100) for one run:

    Q = 100 * C * (wS*S + wV*V + wP*P)

  C  strict completion (0/1): every required task check passed, the file opens
     and renders in LibreOffice, and no critical regression/security finding.
  S  structural score (0-1): mean of the deterministic checks that apply.
  V  visual score (0-1): judge rubric mean, (mean_1to5 - 1) / 4.
  P  preservation score (0-1): edit/transform tasks only.
  Weights: edit/transform wS=0.30 wV=0.40 wP=0.30; create wS=0.40 wV=0.60;
  extract: Q = 100 * C * F1 (no visual component).

The class score is the mean Q over all runs (tasks x reps), so failed runs
count as 0 and completion rate is folded in. Completion rate and conditional
quality (mean Q over completed runs) are reported alongside.
"""
from __future__ import annotations

import math
import random

WEIGHTS = {
    "edit": (0.30, 0.40, 0.30),
    "transform": (0.30, 0.40, 0.30),
    "create": (0.40, 0.60, 0.0),
}
COUNT_KEYS = {"formula_count", "rendered_overflow_frames"}

ADOPT_DELTA = 15.0      # points of Q
ADOPT_LOWER = 5.0       # lower bound of the 90% interval
NATIVE_BELOW = 5.0      # native wins below this gap
COST_RATIO_MAX = 2.0    # Hermes cost per success <= 2x native
FLOOR_CR = 0.50         # usefulness floor on strict completion


def structural(checks: dict) -> float:
    vals = [float(v) for k, v in checks.items()
            if k not in COUNT_KEYS and k != "preservation" and isinstance(v, (int, float)) and 0 <= v <= 1]
    s = sum(vals) / len(vals) if vals else 0.0
    if checks.get("rendered_overflow_frames"):
        s *= 0.8
    return s


def quality(kind: str, completed: bool, checks: dict, visual: float) -> float:
    if not completed:
        return 0.0
    if kind == "extract":
        return 100.0 * float(checks.get("f1", 0.0))
    ws, wv, wp = WEIGHTS[kind]
    s = structural(checks)
    p = float(checks.get("preservation", 1.0))
    return 100.0 * (ws * s + wv * visual + wp * p)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def bootstrap_delta(a: dict, b: dict, iters: int = 2000, seed: int = 7, level: float = 0.90):
    """Cluster bootstrap of mean(Q_a) - mean(Q_b).
    a, b: {task_instance_id: [Q per rep]}; resamples instances, then reps."""
    keys = sorted(set(a) & set(b))
    if not keys:
        return float("nan"), (float("nan"), float("nan"))
    point = mean(q for k in keys for q in a[k]) - mean(q for k in keys for q in b[k])
    rng = random.Random(seed)
    stats = []
    for _ in range(iters):
        ks = [rng.choice(keys) for _ in keys]
        qa = [rng.choice(a[k]) for k in ks for _ in a[k]]
        qb = [rng.choice(b[k]) for k in ks for _ in b[k]]
        stats.append(mean(qa) - mean(qb))
    stats.sort()
    lo = stats[int((1 - level) / 2 * iters)]
    hi = stats[min(iters - 1, int((1 + level) / 2 * iters))]
    return point, (lo, hi)


def cost_per_success(costs: list[float], completed: list[bool]) -> float:
    n = sum(1 for c in completed if c)
    return (sum(costs) / n) if n else math.inf


def decide(delta: float, ci: tuple, cr_h: float, cr_n: float, cps_h: float, cps_n: float,
           critical_h: int, critical_n: int, wanted: bool = True) -> str:
    """Per-class decision between Hermes (A) and native (B). Returns one of:
    adopt-hermes, native, ambiguous-stage2, none-useful, native-only-security.
    critical_* count runs with a SECURITY finding (macro, external link, PDF
    active content, PII leak, sandbox violation). Quality-critical failures
    already score C = 0 and are not a gate here."""
    if critical_h and not critical_n:
        return "native (hermes ineligible: security finding)"
    if max(cr_h, cr_n) < FLOOR_CR:
        return "none-useful (below 50% completion)"
    if critical_h and critical_n:
        return "neither eligible (critical findings in both)"
    lo, hi = ci
    if delta >= ADOPT_DELTA and lo > ADOPT_LOWER and cps_h <= COST_RATIO_MAX * cps_n and wanted:
        return "adopt-hermes"
    if delta < NATIVE_BELOW or (lo <= 0 and delta < ADOPT_DELTA):
        return "native"
    return "ambiguous-stage2"


def spearman(x: list[float], y: list[float]) -> float:
    """Judge-vs-human agreement on the human review sample."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    if len(x) < 2:
        return float("nan")
    rx, ry = ranks(x), ranks(y)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")
