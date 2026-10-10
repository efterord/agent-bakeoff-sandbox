#!/usr/bin/env python3
"""Render-budget wrapper, installed as /opt/pilot/bin/soffice and /opt/pilot/bin/pdftoppm.

soffice : at most PILOT_MAX_RENDERS (4) conversions per run; the 5th call is
          refused with the same message in both arms.
pdftoppm: forces 1280x720 output and at most 10 pages per render.
The counter lives in the per-run tmpfs HOME. (An agent with a shell could
reset it or call the real binaries directly; the gate's distinct-image cap is
the hard limit, and the trace records every render call.)
"""
import os
import sys

REAL = {"soffice": "/usr/bin/soffice", "pdftoppm": "/usr/bin/pdftoppm"}
MAX = int(os.environ.get("PILOT_MAX_RENDERS", "4"))
COUNTER = os.path.join(os.environ.get("HOME", "/tmp"), ".pilot-render-count")


def main():
    name = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if name == "soffice":
        converting = any(a.startswith("--convert-to") for a in args)
        if converting:
            try:
                n = int(open(COUNTER).read().strip() or 0)
            except (OSError, ValueError):
                n = 0
            if n >= MAX:
                sys.stderr.write(f"render budget exhausted: at most {MAX} renders per run\n")
                return 3
            with open(COUNTER, "w") as fh:
                fh.write(str(n + 1))
        os.execv(REAL["soffice"], [REAL["soffice"]] + args)
    if name == "pdftoppm":
        clean, skip = [], False
        for a in args:
            if skip:
                skip = False
                continue
            if a in ("-r", "-scale-to", "-scale-to-x", "-scale-to-y", "-x", "-y", "-W", "-H", "-l", "-f"):
                skip = True
                continue
            clean.append(a)
        os.execv(REAL["pdftoppm"], [REAL["pdftoppm"], "-scale-to-x", "1280", "-scale-to-y", "720", "-l", "10"] + clean)
    sys.stderr.write(f"counting_wrapper: unknown name {name}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
