"""Sealed render container entrypoint (scoring/judging only).

Runs with the same sandbox flags, --network none and NO gate socket. Renders
every /work/in/*.pptx to /work/out/<stem>/deck.pdf and page-N.png with the real
LibreOffice (not the agents' counting wrapper), so fonts and antialiasing are
identical for every arm. A deck that exploits LibreOffice gains nothing here.
"""
import json
import subprocess
import sys
from pathlib import Path

DPI = int(sys.argv[1]) if len(sys.argv) > 1 else 50
res = {}
for deck in sorted(Path("/work/in").glob("*.pptx")):
    od = Path("/work/out") / deck.stem
    od.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["/usr/bin/soffice", "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(od),
                        str(deck)], capture_output=True, timeout=180)
        pdf = od / (deck.stem + ".pdf")
        pages = []
        if pdf.exists() and pdf.stat().st_size > 0:
            subprocess.run(["/usr/bin/pdftoppm", "-r", str(DPI), "-l", "12", "-png", str(pdf), str(od / "page")],
                           capture_output=True, timeout=180)
            pages = sorted(p.name for p in od.glob("page-*.png"))
        res[deck.name] = {"pdf": pdf.exists(), "pages": pages}
    except subprocess.TimeoutExpired:
        res[deck.name] = {"pdf": False, "pages": [], "error": "timeout"}
(Path("/work/out") / "render.json").write_text(json.dumps(res, indent=1))
print(json.dumps(res))
