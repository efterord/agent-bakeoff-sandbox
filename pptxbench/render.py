"""Rendering: LibreOffice headless to PDF, poppler to PNG, thumbnail grids,
and LibreOffice recalculation for workbooks. One private LibreOffice profile per
harness run; calls are sequential (soffice does not like parallel use of one
profile)."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

SOFFICE_TIMEOUT = 120


class Renderer:
    def __init__(self, work: Path):
        self.work = work
        self.profile = work / "lo-profile"
        self.profile.mkdir(parents=True, exist_ok=True)
        self.soffice = shutil.which("soffice") or shutil.which("libreoffice")
        self.pdftoppm = shutil.which("pdftoppm")
        self.seconds = 0.0
        self.calls = 0

    @property
    def available(self) -> bool:
        return bool(self.soffice and self.pdftoppm)

    def _soffice(self, args: list[str], outdir: Path) -> subprocess.CompletedProcess:
        outdir.mkdir(parents=True, exist_ok=True)
        cmd = [self.soffice, f"-env:UserInstallation=file://{self.profile}", "--headless", "--norestore",
               *args, "--outdir", str(outdir)]
        t0 = time.time()
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=SOFFICE_TIMEOUT)
        finally:
            self.seconds += time.time() - t0
            self.calls += 1

    def to_pdf(self, src: Path, outdir: Path) -> Path | None:
        """Convert an office file to PDF. None if LibreOffice cannot open it."""
        if src.suffix.lower() == ".pdf":
            return src
        if not self.soffice:
            return None
        try:
            self._soffice(["--convert-to", "pdf", str(src)], outdir)
        except subprocess.TimeoutExpired:
            return None
        pdf = outdir / (src.stem + ".pdf")
        return pdf if pdf.exists() and pdf.stat().st_size > 0 else None

    def recalc_xlsx(self, src: Path, outdir: Path) -> Path | None:
        """Open in LibreOffice Calc and re-save: formulas are recalculated on
        load and cached values written (same approach as Hermes xlsx_recalc.py)."""
        if not self.soffice:
            return None
        try:
            self._soffice(["--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML", str(src)], outdir)
        except subprocess.TimeoutExpired:
            return None
        out = outdir / (src.stem + ".xlsx")
        return out if out.exists() else None

    def pages(self, pdf: Path, outdir: Path, dpi: int = 40, limit: int = 12) -> list[Path]:
        if not self.pdftoppm or pdf is None:
            return []
        outdir.mkdir(parents=True, exist_ok=True)
        prefix = outdir / "page"
        subprocess.run([self.pdftoppm, "-r", str(dpi), "-l", str(limit), "-png", str(pdf), str(prefix)],
                       capture_output=True, timeout=120)
        return sorted(outdir.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))


def page_count(pdf: Path | None) -> int:
    if pdf is None:
        return 0
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf)).pages)
    except Exception:
        return 0


def grid(images: list[Path], out: Path, cols: int = 4) -> Path | None:
    """Thumbnail grid (contact sheet) for the judge and for humans."""
    if not images:
        return None
    from PIL import Image
    ims = [Image.open(p).convert("RGB") for p in images]
    w = max(i.width for i in ims)
    h = max(i.height for i in ims)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (w + 8) + 8, rows * (h + 8) + 8), "white")
    for k, im in enumerate(ims):
        sheet.paste(im, (8 + (k % cols) * (w + 8), 8 + (k // cols) * (h + 8)))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out
