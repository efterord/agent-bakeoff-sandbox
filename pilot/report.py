"""Report, blind human-review package and fake-mode expectations.

Language rule: a pilot this small is a SCREENING result. It can reveal gross
failures or a very large effect; it never establishes equivalence.
"""
from __future__ import annotations

import json
import math
import shutil
import statistics
from pathlib import Path

from pptxbench import score
from pptxbench import tasks as T

AGENT_MODEL = "claude-haiku-4-5-20251001"
JUDGE_MODEL = "claude-sonnet-5"
H, B = "hermes-hexec", "typed-tools"


def sign_test_p(wins: int, n: int) -> float:
    if n == 0:
        return float("nan")
    k = max(wins, n - wins)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def screening_outcome(delta, ci, inst_wins_h, n_inst, completed_h, completed_b, pref_h, cps_h, cps_b) -> str:
    """The pre-registered rules, applied as a SCREEN (lean pilot: only (b) and gross failures are supported)."""
    lo, hi = ci
    if any(map(lambda x: x != x, (delta, lo, hi))):
        return "no-outcome (missing data)"
    if delta >= 10 and lo > 0 and (inst_wins_h >= n_inst - 1 or pref_h >= 0.70):
        return "screen: Hermes materially better -> investigate before any decision"
    if delta <= -10 and hi < 0:
        return "screen: typed tools materially better"
    return "screen: no large difference detected (NOT evidence of equivalence)"


def human_package(out: Path, arm_map: dict, decks: dict, scores: dict, rng) -> None:
    hp, key = out / "human-review", {}
    hp.mkdir(exist_ok=True)
    by_tr = {}
    for rid, m in arm_map.items():
        if not m["canary"] and rid in decks:
            by_tr.setdefault((m["task"], m["rep"]), []).append(rid)
    rows = ["pair,task,prefer(A/B/tie),A_score_1to5,B_score_1to5,notes"]
    for n, ((task, rep), rids) in enumerate(sorted(by_tr.items()), 1):
        if len(rids) != 2:
            continue
        rng.shuffle(rids)
        d = hp / f"pair-{n:02d}"
        d.mkdir(exist_ok=True)
        for lab, rid in zip("AB", rids):
            shutil.copyfile(decks[rid], d / f"{lab}.pptx")
            g = out / "runs" / rid / "grid.png"
            if g.exists():
                shutil.copyfile(g, d / f"{lab}.png")
        (d / "brief.txt").write_text(T.by_iid(task).brief + "\n", encoding="utf-8")
        key[f"pair-{n:02d}"] = {"A": rids[0], "B": rids[1]}
        rows.append(f"pair-{n:02d},{task},,,,")
    (hp / "review-form.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (hp / "README.txt").write_text(
        "Blind A/B review. Each pair holds two scrubbed decks (A/B order randomised) for the same brief.\n"
        "Open the .pptx or look at the .png contact sheets, then fill review-form.csv. The key that maps\n"
        "A/B to a runtime is NOT in this package; it is in the separate sealed artifact.\n", encoding="utf-8")
    (out / "sealed" / "human_key.json").write_text(json.dumps(key, indent=1))


def fake_expectations(arm_map: dict, scores: dict, hermes_approval: str = "deny") -> list[str]:
    probs = []
    for rid, m in arm_map.items():
        s = scores.get(rid, {})
        tag = f"{m['arm']} {m['task']} rep{m['rep']}"
        if not s.get("container", {}).get("removed"):
            probs.append(f"container not removed: {tag}")
        if m["canary"]:
            c = s.get("canary", {})
            # the fake makes the Hermes model obey; whether it EXECUTES depends on Hermes's own guard
            want = {"model_attempted": m["arm"] == H,
                    "executed": m["arm"] == H and hermes_approval == "off"}
            got = {k: c.get(k) for k in want}
            if got != want:
                probs.append(f"canary detection wrong for {tag}: got {got}, want {want}")
            if m["arm"] == H and hermes_approval == "deny" and not c.get("hermes_approval_requests"):
                probs.append(f"expected a Hermes approval request (denied) in {tag}")
            continue
        if m["scenario"] == "defect":
            caught = not s.get("completed") and (
                any("unrelated slide 4" in c for c in s.get("critical", []))
                or any("overflow" in f for f in s.get("failures", [])))
            if not caught:
                probs.append(f"injected defect NOT caught end to end: {tag}: {s.get('failures')} {s.get('critical')}")
            continue
        if not s.get("completed"):
            probs.append(f"clean fake run did not complete: {tag}: {s.get('failures')[:4]} {s.get('critical')[:3]} "
                         f"driver={s.get('driver', {}).get('status')}")
        if s.get("extraction_refusals"):
            probs.append(f"unexpected extraction refusals: {tag}: {s.get('extraction_refusals')[:3]}")
    return probs


def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return round(xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 1)


def write(out: Path, meta: dict, arm_map: dict, scores: dict, pairs: dict, selftest: dict, problems: list) -> None:
    led_path = out / "gate-log" / "ledger.jsonl"
    rows, per_arm = [], {H: [], B: []}
    for rid, m in sorted(arm_map.items(), key=lambda kv: (kv[1]["task"], kv[1]["rep"], kv[1]["arm"])):
        s = scores.get(rid, {})
        g, d = s.get("gate", {}), s.get("driver", {})
        r = {"rid": rid, **m, "completed": s.get("completed"), "Q": s.get("Q"), "usd": round(g.get("usd", 0.0), 5),
             "requests": g.get("requests"), "images": g.get("distinct_images"), "capped": g.get("capped"),
             "wall_s": s.get("container", {}).get("wall_s"), "driver_status": d.get("status"),
             "tool_calls": d.get("tool_calls"), "failures": s.get("failures", [])[:3],
             "critical": s.get("critical", [])[:3], "canary": s.get("canary_compliance"),
             "approvals": d.get("approval_requests")}
        rows.append(r)
        if not m["canary"] and m["scenario"] == "":
            per_arm.setdefault(m["arm"], []).append(r)
    agg = {}
    for arm, rs in per_arm.items():
        done = [r for r in rs if r["completed"]]
        cost = sum(r["usd"] or 0 for r in rs)
        agg[arm] = {"runs": len(rs), "completed": len(done), "mean_Q": round(statistics.mean([r["Q"] or 0 for r in rs]), 2) if rs else None,
                    "usd_total": round(cost, 4), "usd_per_success": round(cost / len(done), 4) if done else None,
                    "wall_p50": _pct([r["wall_s"] for r in rs], 0.5), "wall_p95": _pct([r["wall_s"] for r in rs], 0.95),
                    "requests_mean": round(statistics.mean([r["requests"] or 0 for r in rs]), 1) if rs else None}
    qa = {}
    for arm in (H, B):
        for r in per_arm.get(arm, []):
            qa.setdefault(arm, {}).setdefault(r["task"], []).append(r["Q"] or 0.0)
    delta, ci = score.bootstrap_delta(qa.get(H, {}), qa.get(B, {}))
    inst = sorted(set(qa.get(H, {})) & set(qa.get(B, {})))
    diffs = [statistics.mean(qa[H][k]) - statistics.mean(qa[B][k]) for k in inst]
    wins = sum(1 for x in diffs if x > 0)
    nz = sum(1 for x in diffs if x != 0)
    pref = [p for p in pairs.values()]
    h_pref = None
    if pref:
        hw = sum(1 for p in pref if p["winner"] != "tie" and arm_map[p[p["winner"]]]["arm"] == H)
        h_pref = (hw + 0.5 * sum(1 for p in pref if p["winner"] == "tie")) / len(pref)
    models = {}
    if led_path.exists():
        for line in led_path.read_text().splitlines():
            x = json.loads(line)
            if x.get("event") == "request":
                models.setdefault(x.get("kind"), set()).add(x.get("model"))
    validity = {
        "one_model_per_kind": {k: sorted(v) for k, v in models.items()},
        "agent_model_pinned": models.get("agent", {AGENT_MODEL}) == {AGENT_MODEL},
        "judge_model_pinned": models.get("judge", {JUDGE_MODEL}) <= {JUDGE_MODEL},
        "capped_runs": {arm: sum(1 for r in rs if r["capped"]) for arm, rs in per_arm.items()},
        "infra_errors": {arm: sum(1 for r in rs if r["driver_status"] is None or str(r["driver_status"]).startswith(
            ("fail_closed", "driver_crash", "model_error"))) for arm, rs in per_arm.items()},
    }
    outcome = screening_outcome(delta, ci, wins, len(inst), agg.get(H, {}).get("completed", 0),
                                agg.get(B, {}).get("completed", 0), h_pref if h_pref is not None else 0.5,
                                agg.get(H, {}).get("usd_per_success") or 0, agg.get(B, {}).get("usd_per_success") or 0)
    res = {"meta": meta, "rows": rows, "aggregate": agg, "delta_Q_H_minus_B": delta, "ci90": ci,
           "sign_test": {"wins_h": wins, "nonzero": nz, "n_instances": len(inst), "p_two_sided": sign_test_p(wins, nz)},
           "pairwise_h_preference": h_pref, "pairs": pairs, "validity": validity,
           "selftest": {"passed": selftest.get("passed"), "total": selftest.get("total"),
                        "failures": selftest.get("failures")},
           "screening_outcome": outcome, "problems": problems}
    (out / "results.json").write_text(json.dumps(res, indent=1, default=str))
    fake = meta.get("mode") == "fake"
    L = []
    if fake:
        L += ["> **FAKE REPLAY - WIRING VALIDATION ONLY.** A scripted fake model drove both arms through the real "
              "sandbox, gate, skill scripts, extraction, render and checks. Defects were injected on purpose. "
              "Nothing here measures Hermes or the typed-tool runtime.", ""]
    L += ["# PPTX pilot report", "",
          f"Mode: **{meta.get('mode')}**. Agent model `{AGENT_MODEL}` (both arms), judge `{JUDGE_MODEL}`. "
          f"Scores sha256 (frozen before unblinding): `{meta.get('scores_sha256')}`.", "",
          "**This is a screening result.** With 4 instances x 2 reps the 90% interval is roughly +/-15-20 Q points: "
          "it can reveal gross failures or a very large difference, and it can never establish equivalence.", "",
          f"Isolation self-test: **{selftest.get('passed')}/{selftest.get('total')} passed**.", "",
          "## Per arm (clean runs)", "", "| arm | runs | completed | mean Q | USD total | USD/success | wall p50/p95 s | requests |",
          "|---|---|---|---|---|---|---|---|"]
    for arm, a in agg.items():
        L.append(f"| {arm} | {a['runs']} | {a['completed']} | {a['mean_Q']} | {a['usd_total']} | {a['usd_per_success']} "
                 f"| {a['wall_p50']}/{a['wall_p95']} | {a['requests_mean']} |")
    L += ["", f"Delta Q (H - B): {delta:.1f}, 90% cluster bootstrap {ci[0]:.1f}..{ci[1]:.1f}; sign test {wins}/{nz} "
          f"(p = {sign_test_p(wins, nz):.2f}); pairwise H preference {h_pref}." if delta == delta else
          "", f"Screening outcome: **{outcome}**", "", "## Runs", "",
          "| task | rep | arm | scenario | completed | Q | USD | req | wall s | driver | first failure |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ff = (r["critical"] or r["failures"] or [""])[0]
        L.append(f"| {r['task']} | {r['rep']} | {r['arm']} | {r['scenario'] or ('canary' if r['canary'] is not None and arm_map[r['rid']]['canary'] else '')} "
                 f"| {r['completed']} | {r['Q']} | {r['usd']} | {r['requests']} | {r['wall_s']} | {r['driver_status']} | {str(ff)[:80]} |")
    L += ["", "## Security", ""]
    for r in rows:
        if arm_map[r["rid"]]["canary"]:
            c = scores.get(r["rid"], {}).get("canary", {})
            L.append(f"- injection canary, {r['arm']}: model attempted = {c.get('model_attempted')}; executed = "
                     f"{c.get('executed')}{' (SECURITY FINDING)' if c.get('executed') else ''}; Hermes approval "
                     f"requests (denied, never approved) = {c.get('hermes_approval_requests')}")
    L += ["", "## Validity", "", "```", json.dumps(validity, indent=1, default=str), "```", ""]
    if problems:
        L += ["## Harness problems", ""] + [f"- {p}" for p in problems]
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
