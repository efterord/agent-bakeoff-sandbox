"""Image-diff metrics for preservation tasks (rendered page vs rendered page)."""
from __future__ import annotations

from pathlib import Path


def _gray(path: Path, size=None):
    import numpy as np
    from PIL import Image
    im = Image.open(path).convert("L")
    if size and im.size != size:
        im = im.resize(size)
    return np.asarray(im, dtype="float64"), im.size


def compare(a: Path, b: Path) -> dict:
    """mad: mean absolute difference (0-1); changed: fraction of pixels whose
    grey level moved by more than 32; ssim: global SSIM on the grey image."""
    import numpy as np
    x, size = _gray(a)
    y, _ = _gray(b, size)
    diff = np.abs(x - y)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mx, my = x.mean(), y.mean()
    vx, vy = x.var(), y.var()
    cov = ((x - mx) * (y - my)).mean()
    ssim = ((2 * mx * my + c1) * (2 * cov + c2)) / ((mx ** 2 + my ** 2 + c1) * (vx + vy + c2))
    return {"mad": float(diff.mean() / 255.0), "changed": float((diff > 32).mean()), "ssim": float(ssim)}


def ink_below(page: Path, box: tuple[float, float, float, float], band: float = 0.08) -> float:
    """Fraction of non-background pixels in a band just below a box given in
    page-relative coordinates (x0, y0, x1, y1). Used as the rendered-extent
    overflow signal: text that spills out of its frame inks this band."""
    import numpy as np
    g, (w, h) = _gray(page)
    bg = float(np.median(g))
    x0, x1 = int(box[0] * w), int(box[2] * w)
    y0, y1 = int(box[3] * h) + 1, int(min(1.0, box[3] + band) * h)
    if y1 <= y0 or x1 <= x0:
        return 0.0
    region = g[y0:y1, x0:x1]
    return float((np.abs(region - bg) > 48).mean())
